"""
Gate S8 — Round-trip verification.

Re-reads the generated xlsx, recomputes debit/credit totals from the
"Account Transactions" sheet, and compares them to the expected values.

Also scans every cell for Excel formula errors (#REF!, #VALUE!, #NAME?, etc.).
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from openpyxl import load_workbook

from app.cards.models import GateResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FORMULA_ERRORS = {"#REF!", "#VALUE!", "#NAME?", "#DIV/0!", "#N/A", "#NUM!", "#NULL!"}

# Column indices in Output B (1-based)
_COL_DEBIT = 11   # K
_COL_CREDIT = 12  # L


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_paise(value) -> int:
    """Convert a cell value (float / str / None) to integer paise."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return round(value * 100)
    # String — may be a formula result when file was not calc'd
    try:
        return round(Decimal(str(value)) * 100)
    except (InvalidOperation, TypeError):
        return 0


def _is_formula_error(value) -> bool:
    if isinstance(value, str):
        return value.strip() in _FORMULA_ERRORS
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_round_trip(
    xlsx_path: Path,
    expected_debits_paise: int,
    expected_credits_paise: int,
) -> GateResult:
    """
    Gate S8: Round-trip check on the generated Output B xlsx.

    Re-reads the xlsx, sums debit (col K) and credit (col L) from the
    "Account Transactions" sheet (data starts at row 3), and compares
    to the expected totals in paise.

    Also flags any Excel formula errors.

    Returns
    -------
    GateResult
        gate="S8", passed=True if totals match and no formula errors found.
    """
    if not Path(xlsx_path).is_file():
        return GateResult(
            gate="S8",
            passed=False,
            detail=f"File not found: {xlsx_path}",
            paise_diff=0,
        )

    try:
        wb = load_workbook(str(xlsx_path), data_only=True)
    except Exception as exc:
        return GateResult(
            gate="S8",
            passed=False,
            detail=f"Could not open xlsx: {exc}",
            paise_diff=0,
        )

    # Locate the main sheet
    sheet_names = wb.sheetnames
    if "Account Transactions" in sheet_names:
        ws = wb["Account Transactions"]
    elif sheet_names:
        ws = wb[sheet_names[0]]
    else:
        return GateResult(
            gate="S8",
            passed=False,
            detail="No sheets found in workbook",
            paise_diff=0,
        )

    total_debits_paise = 0
    total_credits_paise = 0
    formula_errors: list[str] = []
    formula_error_count = 0

    # Data starts at row 3 (row 1 = title, row 2 = headers)
    for row in ws.iter_rows(min_row=3):
        for cell in row:
            if _is_formula_error(cell.value):
                formula_error_count += 1
                if formula_error_count <= 5:  # cap the error list to avoid noise
                    formula_errors.append(
                        f"{cell.coordinate}: {cell.value}"
                    )

        # Sum debit (col K = index 10, 0-based) and credit (col L = index 11)
        if len(row) >= _COL_CREDIT:
            debit_val = row[_COL_DEBIT - 1].value  # 0-indexed
            credit_val = row[_COL_CREDIT - 1].value
            total_debits_paise += _to_paise(debit_val)
            total_credits_paise += _to_paise(credit_val)

    # Compare totals
    debit_diff = total_debits_paise - expected_debits_paise
    credit_diff = total_credits_paise - expected_credits_paise
    paise_diff = abs(debit_diff) + abs(credit_diff)

    issues: list[str] = []

    if debit_diff != 0:
        issues.append(
            f"Debit mismatch: expected {expected_debits_paise} paise, "
            f"got {total_debits_paise} paise (diff {debit_diff:+d})"
        )

    if credit_diff != 0:
        issues.append(
            f"Credit mismatch: expected {expected_credits_paise} paise, "
            f"got {total_credits_paise} paise (diff {credit_diff:+d})"
        )

    if formula_errors:
        extra = f" (+{formula_error_count - len(formula_errors)} more)" if formula_error_count > len(formula_errors) else ""
        issues.append(
            f"Formula errors ({formula_error_count}{extra}): "
            + ", ".join(formula_errors)
        )

    passed = len(issues) == 0

    detail = "; ".join(issues) if issues else (
        f"Round-trip OK — debits={total_debits_paise}p, credits={total_credits_paise}p"
    )

    if passed:
        logger.info("S8 round-trip PASSED: %s", detail)
    else:
        logger.warning("S8 round-trip FAILED: %s", detail)

    return GateResult(
        gate="S8",
        passed=passed,
        detail=detail,
        paise_diff=paise_diff,
    )
