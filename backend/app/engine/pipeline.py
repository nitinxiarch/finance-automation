"""
Main reconciliation engine pipeline.
Orchestrates all steps: parse → normalise → match → classify → gates → result.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Optional

from app.engine.classify import classify_all
from app.engine.gates import run_all_gates
from app.engine.matchers import (
    layer1_exact,
    layer2_reference_group,
    layer3_aggregate,
    layer4_fuzzy,
    layer5_residual,
    reset_match_counter,
)
from app.models import (
    BankRow, BankStatement, BooksExport, BooksRow,
    ExceptionRecord, GateResult, MatchRecord,
    ReconciliationResult, RunStatus,
)


def _compute_books_closing(books_rows: list[BooksRow], books_opening_paise: int) -> int:
    """Compute books closing balance from opening + net flows."""
    net = sum(r.net_paise for r in books_rows)
    return books_opening_paise + net


def _result_hash(result: ReconciliationResult) -> str:
    """Deterministic hash of the result for G8."""
    data = json.dumps({
        "matches": [
            {"mid": m.match_id, "bank": sorted(m.bank_row_ids), "books": sorted(m.books_row_ids)}
            for m in sorted(result.matches, key=lambda m: m.match_id)
        ],
        "exceptions": [
            {"eid": e.exception_id, "code": e.code, "amount": e.amount_paise}
            for e in sorted(result.exceptions, key=lambda e: e.exception_id)
        ],
        "bank_closing": result.bank_closing_paise,
        "books_closing": result.books_closing_paise,
    }, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def reconcile(
    bank_stmt: BankStatement,
    books_export: BooksExport,
    run_id: str,
    books_opening_paise: Optional[int] = None,
    progress_callback=None,
) -> ReconciliationResult:
    """
    Run the full reconciliation pipeline.

    Args:
        bank_stmt: Parsed bank statement.
        books_export: Parsed books export.
        run_id: Unique run identifier.
        books_opening_paise: Optional books opening balance override.
        progress_callback: Optional callable(step_name, detail) for SSE progress.

    Returns:
        ReconciliationResult with all matches, exceptions, and gate results.
    """
    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    reset_match_counter()

    _progress("PARSE", f"Bank: {len(bank_stmt.rows)} rows, Books: {len(books_export.rows)} rows")

    bank_rows = bank_stmt.rows
    books_rows = books_export.rows

    # Build lookup maps
    bank_map: dict[str, BankRow] = {r.row_id: r for r in bank_rows}
    books_map: dict[str, BooksRow] = {r.row_id: r for r in books_rows}

    all_matches: list[MatchRecord] = []
    all_exceptions: list[ExceptionRecord] = []

    # --- Layer 1: Exact ---
    _progress("L1_EXACT", "Matching by Tran.Id, amount, and date…")
    l1_matches, unmatched_bank, unmatched_books = layer1_exact(bank_rows, books_rows)
    all_matches.extend(l1_matches)
    _progress("L1_EXACT", f"Found {len(l1_matches)} exact matches")

    # --- Layer 2: Reference group ---
    _progress("L2_REF_GROUP", "Matching split payments by reference group…")
    l2_matches, l2_exceptions, unmatched_bank, unmatched_books = layer2_reference_group(
        unmatched_bank, unmatched_books
    )
    all_matches.extend(l2_matches)
    all_exceptions.extend(l2_exceptions)
    _progress("L2_REF_GROUP", f"Found {len(l2_matches)} group matches, {len(l2_exceptions)} exceptions")

    # --- Layer 3: Aggregate ---
    _progress("L3_AGGREGATE", "Matching lump-sum journals to bank groups…")
    l3_matches, unmatched_bank, unmatched_books = layer3_aggregate(
        unmatched_bank, unmatched_books
    )
    all_matches.extend(l3_matches)
    _progress("L3_AGGREGATE", f"Found {len(l3_matches)} aggregate matches")

    # --- Layer 4: Fuzzy ---
    _progress("L4_FUZZY", "Fuzzy-matching remaining rows…")
    l4_matches, unmatched_bank, unmatched_books = layer4_fuzzy(
        unmatched_bank, unmatched_books
    )
    all_matches.extend(l4_matches)
    _progress("L4_FUZZY", f"Found {len(l4_matches)} fuzzy matches")

    # --- Layer 5: Residual ---
    _progress("L5_RESIDUAL", "Classifying unmatched rows…")
    exc_start = len(all_exceptions) + 1
    l5_exceptions = layer5_residual(unmatched_bank, unmatched_books, exc_start=exc_start)
    all_exceptions.extend(l5_exceptions)
    _progress("L5_RESIDUAL", f"{len(l5_exceptions)} residual items flagged")

    # --- Classify exceptions ---
    _progress("CLASSIFY", "Assigning owners and codes…")
    all_exceptions = classify_all(all_exceptions, bank_map, books_map)

    # --- Compute balances ---
    bank_opening = bank_stmt.footer_opening_paise
    bank_closing = bank_stmt.footer_closing_paise
    bank_total_deposits = sum(r.deposit_paise for r in bank_rows)
    bank_total_withdrawals = sum(r.withdrawal_paise for r in bank_rows)
    books_total_debits = sum(r.debit_paise for r in books_rows)
    books_total_credits = sum(r.credit_paise for r in books_rows)

    if books_opening_paise is None:
        books_opening_paise = bank_opening  # default: use bank opening

    books_closing = _compute_books_closing(books_rows, books_opening_paise)

    # --- Run gates ---
    _progress("GATES", "Running accuracy gates G1-G8…")
    gate_results = run_all_gates(
        bank_stmt=bank_stmt,
        books_rows=books_rows,
        matches=all_matches,
        exceptions=all_exceptions,
        books_opening_paise=books_opening_paise,
        books_closing_paise=books_closing,
    )

    all_gates_passed = all(g.passed for g in gate_results)
    identity_gate = next((g for g in gate_results if g.gate == "G5_IDENTITY"), None)
    identity_ok = identity_gate.passed if identity_gate else False

    if all_gates_passed:
        status = RunStatus.RECONCILED
    else:
        failed_gates = [g.gate for g in gate_results if not g.passed]
        if "G6_NO_UNEXPLAINED" in failed_gates or "G1_ROW_CONSERVATION" in failed_gates:
            status = RunStatus.FAILED_ACCURACY_GATE
        else:
            status = RunStatus.NEEDS_HUMAN

    _progress("COMPLETE", f"Status: {status}")

    result = ReconciliationResult(
        run_id=run_id,
        status=status,
        bank_opening_paise=bank_opening,
        bank_closing_paise=bank_closing,
        books_opening_paise=books_opening_paise,
        books_closing_paise=books_closing,
        bank_total_deposits_paise=bank_total_deposits,
        bank_total_withdrawals_paise=bank_total_withdrawals,
        books_total_debits_paise=books_total_debits,
        books_total_credits_paise=books_total_credits,
        matches=all_matches,
        exceptions=all_exceptions,
        gate_results=gate_results,
        all_gates_passed=all_gates_passed,
        identity_ok=identity_ok,
    )
    result.result_hash = _result_hash(result)
    return result

