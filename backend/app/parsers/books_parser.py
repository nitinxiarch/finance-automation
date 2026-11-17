"""
Books export parser for Zoho "Account Transactions" (.xls, OLE2 format).
Requires xlrd >= 2.0.1

Detection:
  - Opens as xls via xlrd
  - Cell A1 (multi-line) contains "Account Transactions"
  - Row 2 is the header with column "date"

Raises ParseError with fix hints on any problem.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

try:
    import xlrd
except ImportError:
    raise ImportError(
        "xlrd is required to read the books export (.xls). "
        "Install it with: pip install xlrd>=2.0.1"
    )

from app.models import BooksExport, BooksRow
from app.parsers.bank_parser import ParseError


def _strip_amount_books(val: object, allow_negative: bool = False) -> int:
    """Parse a books amount cell to integer paise. Empty/None → 0."""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        d = Decimal(str(val))
    else:
        s = str(val).strip().replace(",", "")
        if s in ("", "-", "—"):
            return 0
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise ParseError(f"Cannot parse books amount: {val!r}")
    if not allow_negative and d < 0:
        raise ParseError(f"Unexpected negative amount in books: {val!r}")
    return int((d * 100).to_integral_value())


def _parse_date_books(val: object, context: str = "") -> date:
    """Parse dates from books: datetime, date, xlrd float, ISO string, DD/Mon/YYYY, or DD-MM-YYYY."""
    if val is None:
        raise ParseError(f"Missing date{' in ' + context if context else ''}")
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, float):
        raise ParseError(
            f"Date cell is a float ({val}) — the workbook datemode must be passed. "
            "Use _parse_date_books_with_wb()."
        )
    s = str(val).strip()
    if not s:
        raise ParseError(f"Missing date{' in ' + context if context else ''}")

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
        f"Cannot parse books date '{s}'{' in ' + context if context else ''}. "
        "Expected YYYY-MM-DD, DD/Mon/YYYY, or DD-MM-YYYY."
    )


def _xlrd_date(val: float, datemode: int) -> date:
    """Convert xlrd float date to Python date."""
    t = xlrd.xldate_as_datetime(val, datemode)
    return t.date()


def _parse_period_from_title(title: str) -> tuple[Optional[date], Optional[date]]:
    """Extract period from Zoho title like '01 Apr 2026 - 30 Apr 2026'."""
    m = re.search(
        r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s*[-–to]+\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        title
    )
    if not m:
        return None, None
    months = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }

    def _p(s: str) -> Optional[date]:
        parts = s.strip().split()
        if len(parts) == 3:
            mon = parts[1].capitalize()
            if mon in months:
                return date(int(parts[2]), months[mon], int(parts[0]))
        return None

    return _p(m.group(1)), _p(m.group(2))


# Books column names (lowercase, as exported by Zoho)
BOOKS_COLUMNS = [
    "date", "account_name", "transaction_details", "transaction_id",
    "reference_transaction_id", "offset_account_id", "offset_account_type",
    "transaction_type", "reference_number", "entity_number",
    "debit", "credit", "net_amount", "contact_id", "account_id",
    "currency_code", "account_group", "account_type", "location_name",
]


def parse_books_export(file_path: str) -> BooksExport:
    """
    Parse a Zoho Account Transactions .xls file.
    Returns BooksExport. Raises ParseError with fix hints.
    """
    try:
        wb = xlrd.open_workbook(file_path)
    except Exception as e:
        raise ParseError(
            f"Cannot open file as .xls: {e}. "
            "The books file must be in Excel 97-2003 (.xls) format. "
            "Export again from Zoho Books → Reports → Account Transactions."
        )

    ws = wb.sheet_by_index(0)
    datemode = wb.datemode

    if ws.nrows < 2:
        raise ParseError("Books file has fewer than 2 rows.")

    # ---- Row 0: title ----
    title_cell = ws.cell_value(0, 0)
    title = str(title_cell).strip() if title_cell else ""
    if "Account Transactions" not in title:
        raise ParseError(
            f"Cell A1 does not contain 'Account Transactions'. Found: '{title[:80]}'. "
            "Export again from Zoho Books → Reports → Account Transactions."
        )

    organisation = ""
    lines = title.split("\n")
    if lines:
        organisation = lines[0].strip()

    period_from, period_to = _parse_period_from_title(title)

    # ---- Row 1: header ----
    header_row = [str(ws.cell_value(1, j)).strip().lower().replace(" ", "_") for j in range(ws.ncols)]
    col_map: dict[str, int] = {name: j for j, name in enumerate(header_row) if name}

    # Flexible column name matching
    def _find_col(candidates: list[str]) -> Optional[int]:
        for c in candidates:
            if c in col_map:
                return col_map[c]
        return None

    col_date = _find_col(["date"])
    col_account_name = _find_col(["account_name"])
    col_txn_details = _find_col(["transaction_details", "description"])
    col_txn_id = _find_col(["transaction_id"])
    col_ref_txn_id = _find_col(["reference_transaction_id"])
    col_offset_account_id = _find_col(["offset_account_id"])
    col_offset_account_type = _find_col(["offset_account_type"])
    col_txn_type = _find_col(["transaction_type"])
    col_ref_number = _find_col(["reference_number"])
    col_entity_number = _find_col(["entity_number"])
    col_debit = _find_col(["debit"])
    col_credit = _find_col(["credit"])
    col_net_amount = _find_col(["net_amount"])
    col_contact_id = _find_col(["contact_id"])
    col_account_id = _find_col(["account_id"])
    col_currency = _find_col(["currency_code", "currency"])
    col_account_group = _find_col(["account_group"])
    col_account_type = _find_col(["account_type"])
    col_location = _find_col(["location_name"])

    if col_date is None:
        raise ParseError(
            f"'date' column not found in books header: {header_row}. "
            "Re-export from Zoho Books."
        )
    if col_debit is None or col_credit is None:
        raise ParseError(
            f"'debit' or 'credit' column not found in books header: {header_row}. "
            "Re-export from Zoho Books."
        )

    def _cell_str(row_idx: int, col: Optional[int], default: str = "") -> str:
        if col is None:
            return default
        v = ws.cell_value(row_idx, col)
        return str(v).strip() if v is not None else default

    def _cell_amount(row_idx: int, col: Optional[int], allow_negative: bool = False) -> int:
        if col is None:
            return 0
        v = ws.cell_value(row_idx, col)
        return _strip_amount_books(v, allow_negative=allow_negative)

    def _cell_date(row_idx: int, col: int, ctx: str) -> date:
        ctype = ws.cell_type(row_idx, col)
        val = ws.cell_value(row_idx, col)
        if ctype == xlrd.XL_CELL_DATE:
            return _xlrd_date(val, datemode)
        return _parse_date_books(val, ctx)

    books_rows: list[BooksRow] = []
    for i in range(2, ws.nrows):
        # Skip the last blank row
        row_vals = [ws.cell_value(i, j) for j in range(ws.ncols)]
        if all(v is None or str(v).strip() == "" for v in row_vals):
            continue

        ctx = f"books row {i + 1}"
        try:
            txn_date = _cell_date(i, col_date, ctx)
        except ParseError:
            continue  # skip non-data rows (sub-headers, etc.)

        debit = _cell_amount(i, col_debit)
        credit = _cell_amount(i, col_credit)
        net_manual = _cell_amount(i, col_net_amount, allow_negative=True) if col_net_amount else None
        net = debit - credit  # canonical: positive = money into bank

        # Validate net_amount if present
        if net_manual is not None and abs(net_manual - abs(net)) > 1:
            # Some exports store net_amount as absolute; both are acceptable
            pass

        row_id = f"K-{(len(books_rows) + 1):04d}"
        books_rows.append(BooksRow(
            row_id=row_id,
            txn_date=txn_date,
            account_name=_cell_str(i, col_account_name),
            transaction_details=_cell_str(i, col_txn_details),
            transaction_id=_cell_str(i, col_txn_id),
            reference_transaction_id=_cell_str(i, col_ref_txn_id),
            offset_account_id=_cell_str(i, col_offset_account_id),
            offset_account_type=_cell_str(i, col_offset_account_type),
            transaction_type=_cell_str(i, col_txn_type),
            reference_number=_cell_str(i, col_ref_number),
            entity_number=_cell_str(i, col_entity_number),
            debit_paise=debit,
            credit_paise=credit,
            net_paise=net,
            contact_id=_cell_str(i, col_contact_id),
            account_id=_cell_str(i, col_account_id),
            currency_code=_cell_str(i, col_currency, "INR"),
            account_group=_cell_str(i, col_account_group),
            account_type=_cell_str(i, col_account_type),
            location_name=_cell_str(i, col_location),
        ))

    if not books_rows:
        raise ParseError(
            "No data rows found in books export. "
            "Re-export from Zoho Books with transaction data in the selected period."
        )

    return BooksExport(
        organisation=organisation,
        period_from=period_from or books_rows[0].txn_date,
        period_to=period_to or books_rows[-1].txn_date,
        rows=books_rows,
    )


def is_books_export(file_path: str) -> bool:
    """Return True if the file looks like a Zoho books export."""
    try:
        wb = xlrd.open_workbook(file_path)
        ws = wb.sheet_by_index(0)
        if ws.nrows >= 1:
            for r in range(min(10, ws.nrows)):
                for c in range(min(10, ws.ncols)):
                    val = str(ws.cell_value(r, c)).strip()
                    if any(sig in val for sig in ("Account Transactions", "Basis: Accrual", "transaction_details")):
                        return True
        return False
    except Exception:
        pass
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(max_row=10, max_col=10, values_only=True):
            for cell in row:
                if cell and any(sig in str(cell) for sig in ("Account Transactions", "Basis: Accrual", "transaction_details")):
                    return True
    except Exception:
        pass
    return False

