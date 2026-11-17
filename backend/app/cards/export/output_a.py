"""
Output A writer — card-statement ledger format.

Column convention (HDFC_SAMPLE):
  - DEBIT transactions (spends)       → "Payments/Refunds" column
  - CREDIT transactions (cashbacks…)  → "Purchases" column
  This is intentionally the REVERSE of what the column headers suggest,
  matching the actual HDFC printed-statement layout.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.datetime import to_excel as date_to_excel_serial

from app.cards.models import CanonicalTxn, TxnDirection, statement_column_convention

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------------

_HEADER_FONT = Font(bold=True)
_HEADER_FILL = PatternFill(fill_type="solid", fgColor="D9E1F2")

_COLUMNS = [
    "Date",
    "Purchases",
    "Payments/Refunds",
    "Payee",
    "Description",
    "Reference Number",
]


def _apply_header(ws, row: int = 1) -> None:
    for col_idx, col_name in enumerate(_COLUMNS, start=1):
        cell = ws.cell(row=row, column=col_idx, value=col_name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")


def _amount(paise: int) -> Decimal:
    """Convert paise (int) to Decimal rupees."""
    return Decimal(paise) / Decimal(100)


def _excel_date(d: date):
    """Return real Excel date serial number for a Python date."""
    return date_to_excel_serial(d)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_output_a(
    txns: list[CanonicalTxn],
    card_key: str,
    period_from: date,
    period_to: date,
    output_path: Path,
) -> Path:
    """
    Write Output A (card statement ledger) to an xlsx file.

    Parameters
    ----------
    txns:
        Transactions to write, in PDF order (sorted by statement chronologically).
    card_key:
        Card identifier string, e.g. "HDFC-8640-John Doe".
    period_from / period_to:
        Statement period — used to build the file name.
    output_path:
        Directory in which to place the file.

    Returns
    -------
    Path
        Absolute path to the written .xlsx file.
    """
    # Parse card_key into components for the file name
    parts = card_key.split("-", 2)
    issuer = parts[0] if len(parts) > 0 else "UNKNOWN"
    last4 = parts[1] if len(parts) > 1 else "0000"

    first_date = period_from.strftime("%Y%m%d")
    last_date = period_to.strftime("%Y%m%d")
    file_name = f"{issuer}-{last4}_{first_date}_{last_date}.xlsx"
    out_file = Path(output_path) / file_name

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Statement"

    _apply_header(ws, row=1)

    # Sort: transactions should come in PDF order (caller's responsibility),
    # but we also sort chronologically by txn_date as a secondary key so that
    # statements from multiple PDFs appear in the right order.
    sorted_txns = sorted(txns, key=lambda t: t.txn_date)

    for data_row_idx, txn in enumerate(sorted_txns, start=2):
        # --- HDFC_SAMPLE column convention ---
        # DEBIT (spend)   → Payments/Refunds (col 3)
        # CREDIT (refund) → Purchases        (col 2)
        amt = _amount(txn.amount_paise)

        if txn.direction == TxnDirection.DEBIT:
            purchases = None
            payments_refunds = amt
        else:
            purchases = amt
            payments_refunds = None

        # Description / payee: use clean description; payee is left blank
        # (the sample has an empty Payee column; callers may fill it later)
        payee = ""
        description = txn.description_clean or txn.description_raw
        reference = txn.reference or ""

        # Date cell — stored as real Excel date serial
        date_cell = ws.cell(row=data_row_idx, column=1)
        date_cell.value = _excel_date(txn.txn_date)
        date_cell.number_format = "DD/MM/YYYY"

        # Purchases
        ws.cell(row=data_row_idx, column=2, value=float(purchases) if purchases is not None else None)
        # Payments/Refunds
        ws.cell(row=data_row_idx, column=3, value=float(payments_refunds) if payments_refunds is not None else None)
        # Payee
        ws.cell(row=data_row_idx, column=4, value=payee)
        # Description
        ws.cell(row=data_row_idx, column=5, value=description)
        # Reference Number
        ws.cell(row=data_row_idx, column=6, value=reference)

    # Auto-fit column widths (best-effort)
    _autofit_columns(ws)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_file))
    logger.info("Output A written: %s (%d rows)", out_file.name, len(sorted_txns))
    return out_file


from openpyxl.utils import get_column_letter

def _autofit_columns(ws) -> None:
    """Approximate column width auto-fit for readability."""
    for col_idx in range(1, ws.max_column + 1):
        col_letter = get_column_letter(col_idx)
        max_length = 0
        for row_idx in range(1, ws.max_row + 1):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None:
                max_length = max(max_length, len(str(val)))
        ws.column_dimensions[col_letter].width = min(max(max_length + 2, 10), 50)
