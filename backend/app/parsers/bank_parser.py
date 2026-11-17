"""
Bank statement parser for ICICI "Detailed Statement" (.xlsx, sheet "Sheet0").

Detection:
  - Opens as xlsx
  - Sheet named "Sheet0"
  - Metadata block contains "A/C No"
  - Header row has first cell == "S.N."

Raises ParseError with a fix hint on any validation failure.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from app.models import BankRow, BankStatement


class ParseError(Exception):
    """Raised with a human-readable message and fix hint."""
    pass


def _strip_amount(val: object, allow_negative: bool = False) -> Optional[int]:
    """Parse an Indian-formatted amount string to integer paise.
    Returns None if val is None/empty/'-'.
    """
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if s in ("", "-", "—"):
        return None
    try:
        d = Decimal(s)
        if not allow_negative and d < 0:
            raise ParseError(f"Negative amount in bank statement: {val!r}")
        # Convert to integer paise, rounding to avoid float drift
        paise = int((d * 100).to_integral_value())
        return paise
    except InvalidOperation:
        raise ParseError(f"Cannot parse amount: {val!r}")


def _parse_date(val: object, context: str = "") -> date:
    """Parse dates like '01/Apr/2026', '01-04-2026 11:14:26 AM', '2026-04-01', etc."""
    if val is None:
        raise ParseError(f"Missing date{' in ' + context if context else ''}")
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        raise ParseError(f"Missing date{' in ' + context if context else ''}")

    # Strip time component if present (e.g. '01-04-2026 11:14:26 AM' or '2026-04-01T11:14:26')
    date_token = s.split()[0].split("T")[0]

    # 1. DD/Mon/YYYY or DD-Mon-YYYY (e.g. 01/Apr/2026, 1-Apr-2026, 01-April-2026)
    m1 = re.match(r"^(\d{1,2})[/\-]([A-Za-z]{3,9})[/\-](\d{4})$", date_token)
    if m1:
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        mon_str = m1.group(2)[:3].lower()
        if mon_str not in months:
            raise ParseError(f"Unknown month '{m1.group(2)}' in date '{s}'{' in ' + context if context else ''}")
        return date(int(m1.group(3)), months[mon_str], int(m1.group(1)))

    # 2. YYYY-MM-DD or YYYY/MM/DD (e.g. 2026-04-01, 2026/04/01)
    m2 = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$", date_token)
    if m2:
        return date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))

    # 3. DD-MM-YYYY or DD/MM/YYYY (e.g. 01-04-2026, 01/04/2026, 1/4/2026)
    m3 = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$", date_token)
    if m3:
        return date(int(m3.group(3)), int(m3.group(2)), int(m3.group(1)))

    raise ParseError(
        f"Cannot parse date '{s}'{' in ' + context if context else ''}. "
        f"Expected format: DD/Mon/YYYY (e.g. 01/Apr/2026) or DD-MM-YYYY"
    )


def _parse_period(period_str: str) -> tuple[date, date]:
    """Parse 'From 01/04/2026 To 30/04/2026' -> (date, date)."""
    m = re.search(r"From\s+(\S+)\s+To\s+(\S+)", period_str, re.IGNORECASE)
    if not m:
        raise ParseError(
            f"Cannot parse transaction period: '{period_str}'. "
            "Expected format: 'From DD/MM/YYYY To DD/MM/YYYY'"
        )
    return _parse_date(m.group(1), "period from"), _parse_date(m.group(2), "period to")


def parse_bank_statement(file_path: str) -> BankStatement:
    """
    Parse an ICICI Detailed Statement .xlsx file.

    Returns BankStatement with all rows and footer totals.
    Raises ParseError with fix hints on any problem.
    """
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
    except Exception as e:
        raise ParseError(
            f"Cannot open file as .xlsx: {e}. "
            "Ensure the file is an Excel .xlsx, not .xls or .csv."
        )

    ws: Worksheet
    if "Sheet0" in wb.sheetnames:
        ws = wb["Sheet0"]
    else:
        target_ws = None
        for name in wb.sheetnames:
            sheet = wb[name]
            for r in sheet.iter_rows(max_row=30, values_only=True):
                if any(c and "S.N." in str(c) for c in r):
                    target_ws = sheet
                    break
            if target_ws:
                break
        if target_ws is None:
            raise ParseError(
                f"Expected sheet named 'Sheet0' but found: {wb.sheetnames}. "
                "Re-download the statement from ICICI NetBanking."
            )
        ws = target_ws

    rows_iter = list(ws.iter_rows(values_only=True))
    if not rows_iter:
        raise ParseError("Sheet0 is empty.")

    # ---- Parse metadata block ----
    metadata: dict[str, str] = {}
    header_row_idx: Optional[int] = None

    for i, row in enumerate(rows_iter):
        if row[0] is not None and str(row[0]).strip() == "S.N.":
            header_row_idx = i
            break
        # collect metadata k/v pairs
        for j in range(len(row) - 1):
            if row[j] is not None:
                key = str(row[j]).strip().rstrip(":").strip()
                val_cell = row[j + 1]
                if val_cell is not None:
                    metadata[key] = str(val_cell).strip()

    if header_row_idx is None:
        raise ParseError(
            "Cannot find the header row (cell with 'S.N.'). "
            "Re-download the statement without filtering rows."
        )

    # Extract metadata fields
    account_name = metadata.get("Name", "")
    account_number = metadata.get("A/C No", metadata.get("Account No", ""))
    currency = metadata.get("Currency", "INR")
    ifsc = metadata.get("IFSC", "")
    period_raw = metadata.get("Transaction Period", "")
    if not period_raw:
        # try to find it differently
        for row in rows_iter[:header_row_idx]:
            for cell in row:
                if cell and "From" in str(cell) and "To" in str(cell):
                    period_raw = str(cell)
                    break

    if not period_raw:
        raise ParseError(
            "Cannot find 'Transaction Period' in metadata. "
            "Re-download the full statement."
        )
    period_from, period_to = _parse_period(period_raw)

    # ---- Parse data rows ----
    # Header columns
    header = rows_iter[header_row_idx]
    col_map: dict[str, int] = {}
    for j, cell in enumerate(header):
        if cell is not None:
            col_map[str(cell).strip()] = j

    required_cols = [
        "S.N.", "Tran. Id", "Value Date", "Transaction Date",
        "Transaction Posted Date", "Cheque. No./Ref. No.",
        "Transaction Remarks", "Withdrawal Amt (INR)",
        "Deposit Amt (INR)", "Balance (INR)"
    ]
    missing = [c for c in required_cols if c not in col_map]
    if missing:
        raise ParseError(
            f"Missing columns in bank statement: {missing}. "
            "Re-download the statement with all columns visible."
        )

    def _get(row: tuple, col: str) -> object:
        idx = col_map[col]
        return row[idx] if idx < len(row) else None

    bank_rows: list[BankRow] = []
    data_start = header_row_idx + 1

    footer_opening: Optional[int] = None
    footer_withdrawals: Optional[int] = None
    footer_deposits: Optional[int] = None
    footer_closing: Optional[int] = None

    in_footer = False
    for i, row in enumerate(rows_iter[data_start:], start=data_start):
        # Detect blank row (table end)
        if all(c is None or str(c).strip() == "" for c in row):
            in_footer = True
            continue

        if in_footer:
            # Parse footer summary lines like "Opening Bal : 16,45,903.04"
            row_text = " ".join(str(c) for c in row if c is not None)
            if "Opening Bal" in row_text or "Opening Balance" in row_text:
                for c in row:
                    try:
                        amt = _strip_amount(c, allow_negative=True)
                        if amt is not None:
                            footer_opening = amt
                            break
                    except ParseError:
                        continue
            elif "Withdrawl" in row_text or "Withdrawal" in row_text:
                for c in reversed(row):
                    try:
                        amt = _strip_amount(c)
                        if amt is not None:
                            footer_withdrawals = amt
                            break
                    except ParseError:
                        continue
            elif "Deposit" in row_text:
                for c in reversed(row):
                    try:
                        amt = _strip_amount(c)
                        if amt is not None:
                            footer_deposits = amt
                            break
                    except ParseError:
                        continue
            elif "Closing Bal" in row_text or "Closing Balance" in row_text:
                for c in row:
                    try:
                        amt = _strip_amount(c, allow_negative=True)
                        if amt is not None:
                            footer_closing = amt
                            break
                    except ParseError:
                        continue
            continue

        # Data row: check S.N. column
        sn_val = _get(row, "S.N.")
        if sn_val is None or str(sn_val).strip() == "":
            continue
        try:
            sn = int(str(sn_val).strip())
        except ValueError:
            continue

        tran_id = str(_get(row, "Tran. Id") or "").strip()
        ctx = f"row {i + 1} (S.N. {sn})"

        txn_val = _get(row, "Transaction Date")
        txn_date = _parse_date(txn_val, ctx)

        value_val = _get(row, "Value Date")
        value_date = _parse_date(value_val, ctx) if value_val is not None and str(value_val).strip() else txn_date

        posted_val = _get(row, "Transaction Posted Date")
        posted_date = _parse_date(posted_val, ctx) if posted_val is not None and str(posted_val).strip() else txn_date
        cheque_ref = str(_get(row, "Cheque. No./Ref. No.") or "").strip()
        remarks = str(_get(row, "Transaction Remarks") or "").strip()

        withdrawal = _strip_amount(_get(row, "Withdrawal Amt (INR)")) or 0
        deposit = _strip_amount(_get(row, "Deposit Amt (INR)")) or 0
        balance = _strip_amount(_get(row, "Balance (INR)"), allow_negative=True)

        if balance is None:
            raise ParseError(
                f"{ctx}: Balance is missing. Re-download the statement without filters."
            )

        if withdrawal > 0 and deposit > 0:
            raise ParseError(
                f"{ctx}: Row has both a withdrawal ({withdrawal/100:.2f}) and deposit ({deposit/100:.2f}). "
                "Bank file may be corrupted — re-download it."
            )

        row_id = f"B-{sn:04d}"
        bank_rows.append(BankRow(
            row_id=row_id,
            sn=sn,
            tran_id=tran_id,
            value_date=value_date,
            transaction_date=txn_date,
            transaction_posted_date=posted_date,
            cheque_ref=cheque_ref,
            remarks=remarks,
            withdrawal_paise=withdrawal,
            deposit_paise=deposit,
            balance_paise=balance,
        ))

    if not bank_rows:
        raise ParseError(
            "No data rows found in bank statement. "
            "Re-download the statement with transaction data."
        )

    # ---- Internal balance continuity check ----
    for idx, brow in enumerate(bank_rows[1:], start=1):
        prev = bank_rows[idx - 1]
        expected = prev.balance_paise + brow.deposit_paise - brow.withdrawal_paise
        if expected != brow.balance_paise:
            raise ParseError(
                f"Row {brow.sn}: balance {brow.balance_paise/100:.2f} does not follow from "
                f"row {prev.sn} balance {prev.balance_paise/100:.2f} "
                f"(expected {expected/100:.2f}). "
                "Re-download the statement without filters."
            )

    # ---- Footer tie-out ----
    if footer_opening is None or footer_closing is None:
        raise ParseError(
            "Could not parse footer totals (Opening Bal / Closing Bal). "
            "Re-download the full statement."
        )

    computed_withdrawals = sum(r.withdrawal_paise for r in bank_rows)
    computed_deposits = sum(r.deposit_paise for r in bank_rows)
    computed_closing = footer_opening + computed_deposits - computed_withdrawals

    if footer_withdrawals is not None and computed_withdrawals != footer_withdrawals:
        raise ParseError(
            f"Computed withdrawals {computed_withdrawals/100:.2f} ≠ footer {footer_withdrawals/100:.2f}. "
            "Re-download the statement — some rows may be missing."
        )
    if footer_deposits is not None and computed_deposits != footer_deposits:
        raise ParseError(
            f"Computed deposits {computed_deposits/100:.2f} ≠ footer {footer_deposits/100:.2f}. "
            "Re-download the statement — some rows may be missing."
        )
    if computed_closing != footer_closing:
        raise ParseError(
            f"Computed closing balance {computed_closing/100:.2f} ≠ footer closing {footer_closing/100:.2f}. "
            "File may be corrupted or incomplete."
        )

    return BankStatement(
        account_name=account_name,
        account_number=account_number,
        currency=currency,
        period_from=period_from,
        period_to=period_to,
        ifsc=ifsc,
        rows=bank_rows,
        footer_opening_paise=footer_opening,
        footer_withdrawals_paise=footer_withdrawals or computed_withdrawals,
        footer_deposits_paise=footer_deposits or computed_deposits,
        footer_closing_paise=footer_closing,
    )


def is_bank_statement(file_path: str) -> bool:
    """Return True if the file looks like an ICICI bank statement."""
    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        sheet_names = wb.sheetnames
        ws = wb["Sheet0"] if "Sheet0" in sheet_names else wb[sheet_names[0]]
        for row in ws.iter_rows(max_row=30, values_only=True):
            for cell in row:
                if cell and any(sig in str(cell) for sig in ("S.N.", "Detailed Statement", "A/C No:", "A/C No")):
                    return True
        return False
    except Exception:
        return False

