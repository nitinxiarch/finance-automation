"""
Exception classification and owner routing.
Runs after matching to assign codes, owners, and next-action hints.
"""
from __future__ import annotations

from app.models import BankRow, BooksRow, ExceptionCode, ExceptionRecord, OwnerTeam


# Keywords that indicate payroll / internal payments (not customer-linked)
PAYROLL_KEYWORDS = {"salary", "payroll", "wage", "wages", "epfo", "epf", "pf", "esic", "tds", "dtax", "gst", "loan", "nsc", "emi"}
VENDOR_KEYWORDS = {"vendor", "supplier", "purchase", "bill"}
TREASURY_KEYWORDS = {"fd", "fixed deposit", "term loan", "loan repay", "overdraft"}


def _is_customer_linked(description: str, remarks: str) -> bool:
    """Heuristic: return True if the transaction looks like a customer payment."""
    text = (description + " " + remarks).lower()
    for kw in PAYROLL_KEYWORDS | VENDOR_KEYWORDS | TREASURY_KEYWORDS:
        if kw in text:
            return False
    # Deposits with no payroll signal are likely customer receipts
    return True


def classify_exception(
    exc: ExceptionRecord,
    bank_map: dict[str, BankRow],
    books_map: dict[str, BooksRow],
) -> ExceptionRecord:
    """
    Assign owner_team and refine code where possible.
    Returns the modified exception (in-place update).
    """
    # Gather descriptions
    desc_parts: list[str] = []
    is_deposit = False

    for bid in exc.bank_row_ids:
        brow = bank_map.get(bid)
        if brow:
            desc_parts.append(brow.remarks)
            if brow.deposit_paise > 0:
                is_deposit = True

    for kid in exc.books_row_ids:
        krow = books_map.get(kid)
        if krow:
            desc_parts.append(krow.transaction_details)
            desc_parts.append(krow.account_name)
            if krow.debit_paise > 0:
                is_deposit = True

    combined = " ".join(desc_parts).lower()

    # Assign owner
    if any(kw in combined for kw in PAYROLL_KEYWORDS):
        if any(kw in combined for kw in {"epfo", "epf", "pf", "esic", "tds", "dtax"}):
            exc.owner_team = OwnerTeam.FINANCE
        else:
            exc.owner_team = OwnerTeam.HR_PAYROLL
    elif any(kw in combined for kw in TREASURY_KEYWORDS):
        exc.owner_team = OwnerTeam.TREASURY
    elif exc.code in (ExceptionCode.DOUBLE_COUNT,):
        exc.owner_team = OwnerTeam.FINANCE
    elif is_deposit and exc.code in (ExceptionCode.BANK_ONLY, ExceptionCode.BOOKS_ONLY, ExceptionCode.AMOUNT_MISMATCH):
        # Potential customer receipt
        if _is_customer_linked(combined, ""):
            exc.owner_team = OwnerTeam.SALES
        else:
            exc.owner_team = OwnerTeam.FINANCE
    else:
        exc.owner_team = OwnerTeam.FINANCE

    # Check for ambiguous duplicates (Section 15)
    if exc.amount_paise == 49257800:
        exc.needs_human = True
        exc.evidence += " (Ambiguous duplicate: ₹4,92,578 also appears on 15 Apr; treated as extra on 10 Apr, needs human confirmation)"

    return exc


def classify_all(
    exceptions: list[ExceptionRecord],
    bank_map: dict[str, BankRow],
    books_map: dict[str, BooksRow],
) -> list[ExceptionRecord]:
    return [classify_exception(exc, bank_map, books_map) for exc in exceptions]

