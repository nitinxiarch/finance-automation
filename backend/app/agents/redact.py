"""
Data redaction before sending to LLM.
Removes account numbers, IFSC codes, and long digit strings.
"""
from __future__ import annotations

import re


# Patterns to redact
_ACCOUNT_NUM_PAT = re.compile(r"\b\d{9,18}\b")
_IFSC_PAT = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_LONG_DIGITS_PAT = re.compile(r"\b\d{7,}\b")


def redact_text(text: str, mask_names: bool = False) -> str:
    """Remove sensitive identifiers from text."""
    text = _ACCOUNT_NUM_PAT.sub("[ACCT_REDACTED]", text)
    text = _IFSC_PAT.sub("[IFSC_REDACTED]", text)
    text = _LONG_DIGITS_PAT.sub("[NUM_REDACTED]", text)
    return text


def redact_exception_for_llm(exc_dict: dict, mask_names: bool = False) -> dict:
    """
    Prepare an exception dict for LLM consumption.
    Only send fields the agent needs; redact sensitive ones.
    """
    safe = {
        "exception_id": exc_dict.get("exception_id"),
        "code": exc_dict.get("code"),
        "amount_paise": exc_dict.get("amount_paise"),
        "evidence": redact_text(exc_dict.get("evidence", ""), mask_names),
        "owner_team": exc_dict.get("owner_team"),
        "bank_row_ids": exc_dict.get("bank_row_ids", []),
        "books_row_ids": exc_dict.get("books_row_ids", []),
    }
    return safe

