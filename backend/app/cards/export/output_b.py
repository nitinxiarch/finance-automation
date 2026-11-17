"""
Output B writer — Zoho Books Account Transactions import format.

Matches the EXACT 19-column layout of Account_Transactions_2(1).xls export.

Double-entry logic (from Section 7):
  - Spend (DEBIT txn) → increases card liability → books CREDIT
  - Payment/refund/cashback/waiver (CREDIT txn) → reduces liability → books DEBIT
  - net_amount written as Excel formula =K{row}-L{row}
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import to_excel as date_to_excel_serial

from app.cards.models import TxnDirection
from app.cards.categorise import CategorisedTxn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column definitions — 19 columns matching Account_Transactions_2(1).xls
# ---------------------------------------------------------------------------

_HEADERS = [
    "date",                      # A  col 1
    "account_name",              # B  col 2
    "transaction_details",       # C  col 3
    "transaction_id",            # D  col 4
    "reference_transaction_id",  # E  col 5
    "offset_account_id",         # F  col 6
    "offset_account_type",       # G  col 7
    "transaction_type",          # H  col 8
    "reference_number",          # I  col 9
    "entity_number",             # J  col 10
    "debit",                     # K  col 11
    "credit",                    # L  col 12
    "net_amount",                # M  col 13
    "contact_id",                # N  col 14
    "account_id",                # O  col 15
    "currency_code",             # P  col 16
    "account_group",             # Q  col 17
    "account_type",              # R  col 18
    "location_name",             # S  col 19
]

_REVIEW_HEADERS = [
    "txn_id",
    "description",
    "proposed_account",
    "confidence",
    "reason",
    "status",
    "pdf_source",
]

# ---------------------------------------------------------------------------
# transaction_type mapping
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "expense": "expense",
    "vendor_payment": "vendor_payment",
    "card_payment": "card_payment",
    "transfer_fund": "transfer_fund",
    "refund": "refund",
    "expense_refund": "expense_refund",
}

_DEFAULT_TYPE_FOR_DIRECTION = {
    TxnDirection.DEBIT: "expense",
    TxnDirection.CREDIT: "refund",
}


def _resolve_txn_type(cat: CategorisedTxn) -> str:
    if cat.proposed_type in _TYPE_MAP:
        return _TYPE_MAP[cat.proposed_type]
    return _DEFAULT_TYPE_FOR_DIRECTION.get(cat.direction, "expense")


# ---------------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------------

_HEADER_FONT = Font(bold=True)
_HEADER_FILL = PatternFill(fill_type="solid", fgColor="D9E1F2")
_TITLE_FONT = Font(bold=True, size=11)


def _apply_header(ws, row: int, headers: list[str]) -> None:
    for col_idx, name in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col_idx, value=name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")


def _amount(paise: int) -> float:
    return paise / 100.0


def _excel_date(d: date):
    """Return real Excel date serial number."""
    return date_to_excel_serial(d)


def _autofit(ws) -> None:
    for col_idx in range(1, ws.max_column + 1):
        col_letter = get_column_letter(col_idx)
        max_len = 0
        for row_idx in range(2, ws.max_row + 1):  # skip row 1 (merged title)
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 60)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_output_b(
    categorised_txns: list[CategorisedTxn],
    card_key: str,
    org_name: str,
    period_from: date,
    period_to: date,
    output_path: Path,
    location_name: str = "",
    account_name: str = "",
) -> Path:
    """
    Write Output B (Zoho Books account transactions import) to an xlsx file.

    Parameters
    ----------
    categorised_txns:
        Fully categorised transactions.
    card_key:
        Card identifier string, e.g. "HDFC-8640-John Doe".
    org_name:
        Organisation name for the title block.
    period_from / period_to:
        Statement period.
    output_path:
        Directory in which to place the file.
    location_name:
        Optional Zoho Books location name (default "").

    Returns
    -------
    Path
        Absolute path to the written .xlsx file.
    """
    parts = card_key.split("-", 2)
    issuer = parts[0] if len(parts) > 0 else "UNKNOWN"
    last4 = parts[1] if len(parts) > 1 else "0000"
    books_account = parts[2] if len(parts) > 2 else card_key  # fallback

    # Look up books account name from first txn if available
    if categorised_txns:
        # CardRegistryEntry.books_account could be passed via card_key,
        # but we use card_key string as the account_name for now.
        pass

    first_date = period_from.strftime("%Y%m%d")
    last_date = period_to.strftime("%Y%m%d")
    file_name = f"{issuer}-{last4}_books_{first_date}_{last_date}.xlsx"
    out_file = Path(output_path) / file_name

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Account Transactions"

    # Row 1 — Title block (A1 merged across all 19 columns, multi-line)
    title_text = (
        f"{org_name}\n"
        f"Account Transactions\n"
        f"Basis: Accrual\n"
        f"From {period_from} To {period_to}"
    )
    title_cell = ws.cell(row=1, column=1, value=title_text)
    title_cell.font = _TITLE_FONT
    title_cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(_HEADERS))
    ws.row_dimensions[1].height = 70

    # Row 2 — Column headers
    _apply_header(ws, row=2, headers=_HEADERS)

    # Row 3+ — Data
    sorted_txns = sorted(categorised_txns, key=lambda c: c.txn.txn_date)

    review_rows: list[CategorisedTxn] = []

    for data_row_idx, cat in enumerate(sorted_txns, start=3):
        txn = cat.txn

        # Double-entry logic:
        #   DEBIT (spend) → books credit (increases liability)
        #   CREDIT (payment/refund/cashback) → books debit (reduces liability)
        amt = _amount(txn.amount_paise)

        if txn.direction == TxnDirection.DEBIT:
            debit_val = None
            credit_val = amt
        else:
            debit_val = amt
            credit_val = None

        txn_type = _resolve_txn_type(cat)
        transaction_details = cat.proposed_account or (txn.description_clean or txn.description_raw)

        # Date (Excel serial)
        date_cell = ws.cell(row=data_row_idx, column=1)
        date_cell.value = _excel_date(txn.txn_date)
        date_cell.number_format = "DD/MM/YYYY"

        # account_name: books account mapped to this card
        ws.cell(row=data_row_idx, column=2, value=account_name or card_key)

        # transaction_details: proposed expense account or vendor name
        ws.cell(row=data_row_idx, column=3, value=transaction_details)

        # transaction_id — blank (no Zoho ID yet)
        ws.cell(row=data_row_idx, column=4, value="")

        # reference_transaction_id — blank
        ws.cell(row=data_row_idx, column=5, value="")

        # offset_account_id — blank
        ws.cell(row=data_row_idx, column=6, value="")

        # offset_account_type — blank
        ws.cell(row=data_row_idx, column=7, value="")

        # transaction_type
        ws.cell(row=data_row_idx, column=8, value=txn_type)

        # reference_number
        ws.cell(row=data_row_idx, column=9, value=txn.reference or "")

        # entity_number — blank
        ws.cell(row=data_row_idx, column=10, value="")

        # debit (K)
        debit_cell = ws.cell(row=data_row_idx, column=11)
        debit_cell.value = debit_val
        if debit_val is not None:
            debit_cell.number_format = "#,##0.00"

        # credit (L)
        credit_cell = ws.cell(row=data_row_idx, column=12)
        credit_cell.value = credit_val
        if credit_val is not None:
            credit_cell.number_format = "#,##0.00"

        # net_amount (M) = K - L as formula
        k_col = get_column_letter(11)  # K
        l_col = get_column_letter(12)  # L
        net_cell = ws.cell(row=data_row_idx, column=13)
        net_cell.value = f"={k_col}{data_row_idx}-{l_col}{data_row_idx}"
        net_cell.number_format = "#,##0.00"

        # contact_id — blank
        ws.cell(row=data_row_idx, column=14, value="")

        # account_id — blank
        ws.cell(row=data_row_idx, column=15, value="")

        # currency_code
        ws.cell(row=data_row_idx, column=16, value="INR")

        # account_group
        ws.cell(row=data_row_idx, column=17, value="liability")

        # account_type
        ws.cell(row=data_row_idx, column=18, value="credit_card")

        # location_name
        ws.cell(row=data_row_idx, column=19, value=location_name)

        # Collect review items
        if cat.categorise_method == "review" or cat.confidence < 0.85:
            review_rows.append(cat)

    # ---------------------------------------------------------------------------
    # Review sheet
    # ---------------------------------------------------------------------------
    ws_review = wb.create_sheet("Review")
    _apply_header(ws_review, row=1, headers=_REVIEW_HEADERS)

    for ridx, cat in enumerate(review_rows, start=2):
        txn = cat.txn
        pdf_source = txn.source.get("file_hash", "") if txn.source else ""
        ws_review.cell(row=ridx, column=1, value=txn.txn_id)
        ws_review.cell(row=ridx, column=2, value=txn.description_clean or txn.description_raw)
        ws_review.cell(row=ridx, column=3, value=cat.proposed_account)
        ws_review.cell(row=ridx, column=4, value=round(cat.confidence, 4))
        ws_review.cell(row=ridx, column=5, value=cat.categorise_method)
        ws_review.cell(row=ridx, column=6, value="PENDING")
        ws_review.cell(row=ridx, column=7, value=pdf_source)

    _autofit(ws)
    _autofit(ws_review)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_file))
    logger.info(
        "Output B written: %s (%d rows, %d review items)",
        out_file.name,
        len(sorted_txns),
        len(review_rows),
    )
    return out_file
