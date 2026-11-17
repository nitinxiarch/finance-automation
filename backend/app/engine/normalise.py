"""
Normalisation helpers.
Converts raw parsed rows into canonical form for matching.
All amounts already in integer paise from parsers.
"""
from __future__ import annotations

import re
from app.models import BankRow, BooksRow


# Category signals for L3 aggregate matching
CATEGORY_SIGNALS = {
    "EPFO": re.compile(r"EPFO|EPF|PF\s", re.IGNORECASE),
    "DTAX": re.compile(r"DTAX|GIB|TAX\s*PAYMENT|TDS|GST\s*PAY", re.IGNORECASE),
    "IMPS_SALARY": re.compile(r"IMPS|SALARY|PAYROLL|WAGES", re.IGNORECASE),
    "NEFT_BULK": re.compile(r"BULD|BULK|NEFT\s*SALARY", re.IGNORECASE),
}


def normalise_text(s: str) -> str:
    """Clean text for fuzzy comparison: lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", s.strip().lower())


def category_signal(remarks: str) -> str:
    """Return a category tag from bank remarks, or '' if none."""
    for tag, pat in CATEGORY_SIGNALS.items():
        if pat.search(remarks):
            return tag
    return ""


def clean_reference(ref: str) -> str:
    """Normalise a reference number for matching (strip whitespace)."""
    return ref.strip().upper()

