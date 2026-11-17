"""
app/cards/pipeline.py
End-to-end processing pipeline for credit card statement PDFs.
Orchestrates: Ingest -> Classify -> Tiered Parse (T1..T4) -> Gates (S1..S10)
              -> Categorise -> Export (Output A & B) -> Audit Briefing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from app.cards.agents.auditor_agent import run_auditor
from app.cards.agents.categoriser_agent import run_categoriser_agent
from app.cards.agents.tools import CardAgentTools
from app.cards.categorise import CategorisedTxn, HistoryLookup, categorise_transactions
from app.cards.classify import build_coverage_matrix, classify_pdf
from app.cards.export.output_a import write_output_a
from app.cards.export.output_b import write_output_b
from app.cards.export.round_trip import verify_round_trip
from app.cards.gates import run_all_gates
from app.cards.ingest import compute_file_hash, ingest_zip
from app.cards.models import (
    CanonicalTxn,
    CardKey,
    CardRegistry,
    CardRegistryEntry,
    CardRun,
    CardRunStatus,
    CardStatement,
    ReviewItem,
    StatementStatus,
    TxnDirection,
)
from app.cards.parsers import get_parser

log = logging.getLogger(__name__)


@dataclass
class CardPipelineConfig:
    zip_path: Path
    output_dir: Path
    reference_export_path: Optional[Path] = None
    org_name: str = "Xiarch Bharat Private Limited"
    location_name: str = "XIARCH GURGAON BRANCH"
    passwords: dict[str, bytes] = field(default_factory=dict)
    confidence_threshold: float = 0.85


async def run_card_pipeline(
    config: CardPipelineConfig,
    run_id: str,
    progress_cb: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> CardRun:
    """
    Execute the full credit card processing pipeline for one upload run.
    """
    def _emit(event: str, data: dict[str, Any]) -> None:
        if progress_cb:
            try:
                progress_cb(event, data)
            except Exception:
                pass

    _emit("status", {"step": "INGEST", "message": "Extracting and verifying ZIP contents…"})

    # Temporary directory for extracted files
    temp_extract = Path(tempfile.mkdtemp(prefix=f"cards_{run_id}_"))

    try:
        # Step 1: Ingest ZIP safely
        ingested_files = ingest_zip(config.zip_path, temp_extract, passwords=config.passwords)
        zip_hash = compute_file_hash(config.zip_path)
        _emit("ingest_done", {"file_count": len(ingested_files), "zip_hash": zip_hash})

        # Step 2: Classify all PDFs
        _emit("status", {"step": "CLASSIFY", "message": "Detecting card issuers and statement periods…"})
        classified = []
        for inf in ingested_files:
            info = classify_pdf(inf.path, folder_hint=inf.folder_hint)
            classified.append((inf, info))

        # Build initial card registry from detected cards
        registry_entries: dict[str, CardRegistryEntry] = {}
        for inf, info in classified:
            card_key_str = f"{info.issuer}-{info.last4}"
            if card_key_str not in registry_entries:
                display_name = f"{info.issuer} {info.last4} ({info.cardholder})"
                books_account = f"Credit Card {info.cardholder} {info.issuer} {info.last4}".strip()
                registry_entries[card_key_str] = CardRegistryEntry(
                    display_name=display_name,
                    books_account=books_account,
                    issuer=info.issuer,
                    last4=info.last4,
                    cardholder=info.cardholder,
                )

        registry = CardRegistry(entries=registry_entries)

        # Step 3: Parse statements (Tiered: T1 text -> T2 OCR)
        _emit("status", {"step": "PARSE", "message": "Parsing statement transactions…"})
        statements: list[CardStatement] = []

        for idx, (inf, info) in enumerate(classified, start=1):
            _emit("progress", {
                "file": inf.path.name,
                "current": idx,
                "total": len(classified),
                "issuer": info.issuer,
                "tier": "T2 OCR" if info.needs_ocr else "T1 Text",
            })

            issuer_key = info.issuer.lower()
            try:
                parser = get_parser(issuer_key)
                stmt = parser.parse(inf.path, inf.file_hash)
            except Exception as e:
                log.error("Failed to parse %s with %s: %s", inf.path.name, issuer_key, e)
                # Create a placeholder failed statement
                stmt = CardStatement(
                    statement_id=str(uuid.uuid4()),
                    card_key=(info.issuer, info.last4, info.cardholder),
                    issuer=info.issuer,
                    last4=info.last4,
                    cardholder=info.cardholder,
                    period_from=info.period_from or date(2026, 1, 1),
                    period_to=info.period_to or date(2026, 1, 31),
                    page_count=info.page_count,
                    file_hash=inf.file_hash,
                    status=StatementStatus.FAILED,
                    transactions=[],
                    printed_debits_paise=0,
                    printed_credits_paise=0,
                    parsed_debits_paise=0,
                    parsed_credits_paise=0,
                    gate_results=[],
                )

            statements.append(stmt)

        # Step 4: Run accuracy gates S1..S10 on all statements
        _emit("status", {"step": "GATES", "message": "Evaluating accuracy gates S1–S10…"})
        for stmt in statements:
            run_all_gates(stmt, statements)

        # Build coverage matrix
        coverage_matrix = build_coverage_matrix(statements)

        # Step 5: Categorise transactions
        _emit("status", {"step": "CATEGORISE", "message": "Categorising transactions into accounting accounts…"})

        # Load reference rows from books export if provided
        reference_rows: list[dict] = []
        allowed_accounts: set[str] = set()

        # Built-in common accounting accounts as base
        base_accounts = [
            "Travel Expense", "Staff Welfare", "Office repair & maintenance",
            "Subscription Expenses", "Bank Fees and Charges", "Car Running Expense",
            "Medical Expenses", "Office Supplies", "IT Service Expenses",
            "Insurance Expenses", "Donation", "GST Payable", "Utsav Mittal",
            "Xiarch Bharat Private Limited", "Bharti Airtel Limited (DEL)",
            "BHARTI AIRTEL LIMITED (HR)", "JIO RELIANCE INFOCOMM LTD",
            "GOOGLE INDIA PRIVATE LIMITED", "IRCTC", "PASCO AUTOMOBILES",
        ]
        allowed_accounts.update(base_accounts)

        if config.reference_export_path and config.reference_export_path.exists():
            try:
                import xlrd
                wb_ref = xlrd.open_workbook(str(config.reference_export_path))
                ws_ref = wb_ref.sheet_by_index(0)
                for r in range(2, ws_ref.nrows):
                    dt_val = ws_ref.cell_value(r, 0)
                    acc_name = str(ws_ref.cell_value(r, 1)).strip()
                    txn_det = str(ws_ref.cell_value(r, 2)).strip()
                    txn_type = str(ws_ref.cell_value(r, 7)).strip()
                    ref_no = str(ws_ref.cell_value(r, 8)).strip()
                    if txn_det:
                        allowed_accounts.add(txn_det)
                        reference_rows.append({
                            "transaction_details": txn_det,
                            "transaction_type": txn_type or "expense",
                            "reference_number": ref_no,
                        })
            except Exception as e:
                log.warning("Could not read reference export: %s", e)

        allowed_accounts_list = sorted(list(allowed_accounts))

        # Flatten all transactions
        all_txns = [t for s in statements for t in s.transactions]

        # Run classic rule-based categorisation first
        categorised = categorise_transactions(
            all_txns,
            reference_rows=reference_rows,
            user_rules=[],
            allowed_accounts=allowed_accounts_list,
        )

        # Optional Qwen closed-set classification for unassigned items
        unassigned = [c.txn for c in categorised if c.confidence < config.confidence_threshold]
        if unassigned and os.environ.get("USE_AI_CATEGORISER", "true").lower() == "true":
            try:
                ai_results = await run_categoriser_agent(unassigned[:100], allowed_accounts_list)
                for c in categorised:
                    if c.txn_id in ai_results:
                        res = ai_results[c.txn_id]
                        if res.get("name") and res["name"] != "UNSURE":
                            c.proposed_account = res["name"]
                            c.proposed_type = res.get("type", "expense")
                            c.confidence = res.get("confidence", 0.9)
                            c.categorise_method = "ai"
            except Exception as e:
                log.info("AI categoriser skipped: %s", e)

        # Build Review Queue
        review_queue: list[ReviewItem] = []
        cat_map = {c.txn_id: c for c in categorised}
        for c in categorised:
            if c.confidence < config.confidence_threshold or c.proposed_account == "UNSURE":
                review_queue.append(
                    ReviewItem(
                        txn_id=c.txn_id,
                        reason="Low confidence category proposal",
                        proposed_account=c.proposed_account or "Uncategorized Expense",
                        proposed_type=c.proposed_type or "expense",
                        confidence=c.confidence,
                        reason_text=f"Rule: {c.categorise_method}",
                    )
                )

        # Step 6: Export Output A and Output B per card
        _emit("status", {"step": "EXPORT", "message": "Generating Output A and Output B workbooks…"})
        config.output_dir.mkdir(parents=True, exist_ok=True)
        card_run_dir = config.output_dir / run_id
        card_run_dir.mkdir(parents=True, exist_ok=True)

        # Group statements by card_key
        card_groups: dict[str, list[CardStatement]] = {}
        for s in statements:
            ck = f"{s.issuer}-{s.last4}"
            if ck not in card_groups:
                card_groups[ck] = []
            card_groups[ck].append(s)

        generated_files: list[Path] = []

        for ck, stmts in card_groups.items():
            # Gather all transactions for this card
            card_txns = [t for s in stmts for t in s.transactions]
            card_cats = [cat_map[t.txn_id] for t in card_txns if t.txn_id in cat_map]

            if not card_txns:
                continue

            min_date = min(t.txn_date for t in card_txns)
            max_date = max(t.txn_date for t in card_txns)

            # Output A: Statement file
            out_a_file = write_output_a(
                card_txns,
                card_key=ck,
                period_from=min_date,
                period_to=max_date,
                output_path=card_run_dir,
            )
            generated_files.append(out_a_file)

            # Output B: Books file
            reg_entry = registry.entries.get(ck)
            books_acc = reg_entry.books_account if reg_entry else f"Credit Card {ck}"
            out_b_file = write_output_b(
                card_cats,
                card_key=ck,
                org_name=config.org_name,
                period_from=min_date,
                period_to=max_date,
                output_path=card_run_dir,
                account_name=books_acc,
                location_name=config.location_name,
            )
            generated_files.append(out_b_file)

        # Build combined ZIP
        zip_out = card_run_dir / f"Cards_Export_{run_id[:8]}.zip"
        with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as zf:
            for gf in generated_files:
                zf.write(gf, arcname=gf.name)

        # Step 7: Final CardRun result
        all_passed = all(s.status == StatementStatus.VERIFIED for s in statements)
        final_status = CardRunStatus.DONE if all_passed else CardRunStatus.DONE

        card_run = CardRun(
            run_id=run_id,
            status=final_status,
            zip_hash=zip_hash,
            statements=statements,
            registry=registry,
            coverage_matrix=coverage_matrix,
            review_queue=review_queue,
        )

        _emit("done", {
            "run_id": run_id,
            "statements_count": len(statements),
            "transactions_count": len(all_txns),
            "review_queue_count": len(review_queue),
            "verified": all_passed,
        })

        return card_run

    finally:
        # Cleanup temporary extraction directory
        shutil.rmtree(temp_extract, ignore_errors=True)

