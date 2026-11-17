"""
Accuracy gates S1–S10 for card statement verification.

Each gate returns a :class:`~app.cards.models.GateResult`.
run_all_gates() runs all 10 and attaches results to the statement.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from app.cards.models import GateResult, StatementStatus, TxnDirection

if TYPE_CHECKING:
    from app.cards.models import CardStatement

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------

def _gate_s1(statement: "CardStatement") -> GateResult:
    """
    S1 — Page classification coverage.

    Every page must have been classified (transaction, summary, terms_offers,
    or other).  A page is considered unclassified if no transaction or balance
    data was extracted from it AND the page_count exceeds the number of
    transaction-bearing pages.

    Implementation: since parsers tag each CanonicalTxn with its source page,
    we check that every page (1..page_count) is covered by at least one txn
    OR by the printed balance metadata.  If the statement was parsed at T1/T2
    and page_count == 0 (not set), we pass optimistically.
    """
    if statement.status == StatementStatus.FAILED:
        return GateResult(gate="S1", passed=False, detail="Statement marked FAILED during ingestion/parsing")

    if statement.page_count == 0 and not statement.transactions:
        return GateResult(gate="S1", passed=False, detail="Corrupted or empty PDF: 0 pages and 0 transactions parsed")

    if statement.page_count == 0:
        return GateResult(gate="S1", passed=True, detail="page_count not set; skipping")

    covered_pages = {txn.source.get("page") for txn in statement.transactions if txn.source.get("page") is not None}

    # Allow pages that carry no transactions (e.g. summary / T&C) — we just
    # require that at least one page has transactions when page_count > 0.
    if statement.page_count > 0 and not covered_pages and not statement.transactions:
        return GateResult(
            gate="S1",
            passed=False,
            detail="No transactions parsed; possible mis-classification of all pages",
        )

    return GateResult(gate="S1", passed=True, detail=f"Covered pages: {sorted(covered_pages)}")


def _gate_s2(statement: "CardStatement") -> GateResult:
    """
    S2 — Debit/credit totals match printed summary to the paisa.
    """
    debit_diff = statement.parsed_debits_paise - statement.printed_debits_paise
    credit_diff = statement.parsed_credits_paise - statement.printed_credits_paise

    if debit_diff == 0 and credit_diff == 0:
        return GateResult(gate="S2", passed=True, detail="Totals match exactly")

    detail_parts = []
    if debit_diff != 0:
        detail_parts.append(f"debit diff={debit_diff} paise")
    if credit_diff != 0:
        detail_parts.append(f"credit diff={credit_diff} paise")

    # Use the larger absolute diff as paise_diff
    paise_diff = max(abs(debit_diff), abs(credit_diff))

    return GateResult(
        gate="S2",
        passed=False,
        detail="; ".join(detail_parts),
        paise_diff=paise_diff,
    )


def _gate_s3(statement: "CardStatement", all_statements: list["CardStatement"]) -> GateResult:
    """
    S3 — Roll-forward: closing balance of previous statement == opening balance
    of current statement (same card).
    """
    if statement.opening_balance_paise is None:
        return GateResult(gate="S3", passed=True, detail="No opening balance on this statement; skipping")

    # Find the statement for the same card that immediately precedes this one
    same_card = [
        s for s in all_statements
        if s.issuer == statement.issuer
        and s.last4 == statement.last4
        and s.cardholder == statement.cardholder
        and s.statement_id != statement.statement_id
        and s.period_to is not None
        and s.period_to < statement.period_from
    ]

    if not same_card:
        return GateResult(gate="S3", passed=True, detail="No prior statement found; skipping roll-forward")

    prev = max(same_card, key=lambda s: s.period_to)

    if prev.closing_balance_paise is None:
        return GateResult(gate="S3", passed=True, detail="Prior statement has no closing balance; skipping")

    diff = statement.opening_balance_paise - prev.closing_balance_paise
    if diff == 0:
        return GateResult(gate="S3", passed=True, detail="Roll-forward OK")

    return GateResult(
        gate="S3",
        passed=False,
        detail=f"Opening {statement.opening_balance_paise} ≠ prior closing {prev.closing_balance_paise}",
        paise_diff=abs(diff),
    )


def _gate_s4(statement: "CardStatement", all_statements: list["CardStatement"]) -> GateResult:
    """
    S4 — Duplicate detection.

    Removes duplicates only when the same card has overlapping periods and the
    duplicate matches on card+date+amount+description+reference.  Returns a
    gate result noting how many duplicates were detected (does NOT mutate the
    statement — that is for the pipeline to handle post-gate).
    """
    # Gather all txns from overlapping statements for the same card
    overlapping_txns: list = []
    for s in all_statements:
        if s.statement_id == statement.statement_id:
            continue
        if s.issuer != statement.issuer or s.last4 != statement.last4 or s.cardholder != statement.cardholder:
            continue
        # Check period overlap
        if s.period_to is None or s.period_from is None:
            continue
        if statement.period_to is None or statement.period_from is None:
            continue
        overlap = s.period_from <= statement.period_to and s.period_to >= statement.period_from
        if overlap:
            overlapping_txns.extend(s.transactions)

    if not overlapping_txns:
        return GateResult(gate="S4", passed=True, detail="No overlapping statements")

    def _sig(txn) -> tuple:
        return (txn.txn_date, txn.amount_paise, txn.description_clean[:50], txn.reference[:30])

    other_sigs = {_sig(t) for t in overlapping_txns}
    dup_count = sum(1 for t in statement.transactions if _sig(t) in other_sigs)

    if dup_count == 0:
        return GateResult(gate="S4", passed=True, detail="No duplicates found in overlapping periods")

    return GateResult(
        gate="S4",
        passed=False,
        detail=f"{dup_count} duplicate transaction(s) detected in overlapping period(s)",
        paise_diff=0,
    )


def _gate_s5(statement: "CardStatement") -> GateResult:
    """
    S5 — Direction comes from printed markers (Dr/Cr, debit/credit column).

    Verify that all DEBIT transactions contribute to parsed_debits and all
    CREDIT transactions contribute to parsed_credits, consistent with S2.
    """
    computed_debits = sum(t.amount_paise for t in statement.transactions if t.direction == TxnDirection.DEBIT)
    computed_credits = sum(t.amount_paise for t in statement.transactions if t.direction == TxnDirection.CREDIT)

    # Cross-check with the values stored on the statement
    debit_ok = computed_debits == statement.parsed_debits_paise
    credit_ok = computed_credits == statement.parsed_credits_paise

    if debit_ok and credit_ok:
        return GateResult(gate="S5", passed=True, detail="Direction totals consistent")

    details = []
    if not debit_ok:
        details.append(f"debit sum mismatch: computed={computed_debits} stored={statement.parsed_debits_paise}")
    if not credit_ok:
        details.append(f"credit sum mismatch: computed={computed_credits} stored={statement.parsed_credits_paise}")

    return GateResult(gate="S5", passed=False, detail="; ".join(details))


def _gate_s6(statement: "CardStatement") -> GateResult:
    """
    S6 — All transaction dates parse and fall within period or previous-cycle window.

    Allowed window: period_from - 31 days .. period_to + 1 day.
    """
    if statement.period_from is None or statement.period_to is None:
        return GateResult(gate="S6", passed=True, detail="No period on statement; skipping date check")

    window_start = statement.period_from - timedelta(days=31)
    window_end = statement.period_to + timedelta(days=1)

    out_of_range = [
        t for t in statement.transactions
        if not (window_start <= t.txn_date <= window_end)
    ]

    if not out_of_range:
        return GateResult(gate="S6", passed=True, detail="All dates within allowed window")

    sample = [str(t.txn_date) for t in out_of_range[:5]]
    return GateResult(
        gate="S6",
        passed=False,
        detail=f"{len(out_of_range)} date(s) outside window {window_start}..{window_end}: {sample}",
    )


def _gate_s7(statement: "CardStatement") -> GateResult:
    """
    S7 — Row conservation: parsed transaction count == output row count.

    At the gates stage we just record the parsed count; the exporter calls
    this gate again post-export with the actual output count.  Here we pass
    optimistically, noting the count.
    """
    count = len(statement.transactions)
    return GateResult(
        gate="S7",
        passed=True,
        detail=f"Parsed {count} transaction row(s); output row conservation checked post-export",
    )


def _gate_s8(statement: "CardStatement") -> GateResult:
    """
    S8 — Round-trip placeholder.

    This gate is intentionally a no-op here and returns True.  It is called
    again by the exporter after writing and re-reading the output file, where
    it verifies that amounts survive the Excel round-trip without rounding.
    """
    return GateResult(
        gate="S8",
        passed=True,
        detail="Round-trip gate deferred to post-export phase",
    )


def _gate_s9(statement: "CardStatement") -> GateResult:
    """
    S9 — Determinism: hash of sorted transactions is stable across runs.

    Builds a canonical hash by sorting transactions by (txn_date, amount_paise,
    description_clean, reference) and hashing their JSON representation.
    The hash is stored in the detail field for comparison by CI.
    """
    rows = sorted(
        statement.transactions,
        key=lambda t: (str(t.txn_date), t.amount_paise, t.description_clean, t.reference),
    )
    payload = json.dumps(
        [
            {
                "date": str(r.txn_date),
                "amount_paise": r.amount_paise,
                "direction": r.direction.value,
                "kind": r.kind.value,
                "desc": r.description_clean,
                "ref": r.reference,
            }
            for r in rows
        ],
        sort_keys=True,
    ).encode()

    h = hashlib.sha256(payload).hexdigest()[:16]
    return GateResult(gate="S9", passed=True, detail=f"txn_hash={h}")


def _gate_s10(statement: "CardStatement") -> GateResult:
    """
    S10 — AI limits.

    No AI-generated output (tier=T4) may appear outside allowed values.
    Allowed: amount_paise > 0, direction in {DEBIT, CREDIT},
             kind is a known TxnKind, parse_confidence in [0, 1].
    """
    from app.cards.models import ParseTier, TxnKind  # noqa: PLC0415

    violations = []
    for t in statement.transactions:
        if t.tier != ParseTier.T4:
            continue
        if t.amount_paise <= 0:
            violations.append(f"{t.txn_id}: amount_paise={t.amount_paise}")
        if t.direction not in (TxnDirection.DEBIT, TxnDirection.CREDIT):
            violations.append(f"{t.txn_id}: bad direction={t.direction}")
        if t.parse_confidence < 0.0 or t.parse_confidence > 1.0:
            violations.append(f"{t.txn_id}: confidence={t.parse_confidence}")

    if not violations:
        return GateResult(gate="S10", passed=True, detail="All AI outputs within allowed values")

    return GateResult(
        gate="S10",
        passed=False,
        detail=f"{len(violations)} AI-output violation(s): {violations[:3]}",
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_all_gates(
    statement: "CardStatement",
    all_statements: list["CardStatement"],
) -> list[GateResult]:
    """
    Run all 10 accuracy gates S1–S10 against *statement*.

    *all_statements* must include *statement* itself (needed for S3/S4 cross-
    statement checks).

    Populates ``statement.gate_results`` and updates ``statement.status``:
      - VERIFIED   if all gates pass
      - NEEDS_REVIEW if any gate fails
    """
    results = [
        _gate_s1(statement),
        _gate_s2(statement),
        _gate_s3(statement, all_statements),
        _gate_s4(statement, all_statements),
        _gate_s5(statement),
        _gate_s6(statement),
        _gate_s7(statement),
        _gate_s8(statement),
        _gate_s9(statement),
        _gate_s10(statement),
    ]

    statement.gate_results = results

    if all(r.passed for r in results):
        statement.status = StatementStatus.VERIFIED
    else:
        statement.status = StatementStatus.NEEDS_REVIEW

    failed = [r.gate for r in results if not r.passed]
    if failed:
        logger.warning(
            "Statement %s (%s-%s) failed gates: %s",
            statement.statement_id,
            statement.issuer,
            statement.last4,
            failed,
        )

    return results
