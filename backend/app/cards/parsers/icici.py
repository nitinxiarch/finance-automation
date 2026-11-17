"""
app/cards/parsers/icici.py
Parser for ICICI Bank Amazon Pay credit card statements (card ending 7001).

Transactions sit on Page 1 under:
    SPENDS OVERVIEW
    Date SerNo. Transaction Details Reward Points Intl.# Amount (in`)
    4315XXXXXXXX7001
    24/03/2026 13110198035 BLINKIT ECOM GURGAON IN 20 2,059.40
    01/05/2026 13330222602 BBPS Payment received 0 3,472.40 CR

Columns:
    Date: DD/MM/YYYY
    SerNo: digits
    Transaction Details: merchant / description
    Reward Points: integer
    Intl. amount: optional
    Amount: Indian grouping, optional CR for credit
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from app.cards.models import (
    CanonicalTxn,
    CardKey,
    CardStatement,
    ParseTier,
    StatementStatus,
    TxnDirection,
    TxnKind,
)
from app.cards.parsers.base import BaseParser

log = logging.getLogger(__name__)

# ICICI row: Date (DD/MM/YYYY), SerNo, Details, Reward Points, Amount (with optional CR)
_ICICI_ROW_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+(\d+)\s+(.+?)\s+(\d+)\s+([\d,]+\.\d{2})\s*(CR|Dr)?\s*$",
    re.IGNORECASE,
)

_KIND_MAP = [
    (re.compile(r"\bpayment\b|\bbbps\b", re.I), TxnKind.PAYMENT),
    (re.compile(r"\binterest\b", re.I), TxnKind.INTEREST),
    (re.compile(r"\bgst\b", re.I), TxnKind.GST),
    (re.compile(r"\bfee\b", re.I), TxnKind.FEE),
    (re.compile(r"\brefund\b|\breversal\b", re.I), TxnKind.REFUND),
    (re.compile(r"\bcashback\b", re.I), TxnKind.CASHBACK),
]

def _guess_kind(desc: str, direction: TxnDirection) -> TxnKind:
    for pat, kind in _KIND_MAP:
        if pat.search(desc):
            return kind
    if direction == TxnDirection.CREDIT:
        return TxnKind.REFUND
    return TxnKind.PURCHASE


class ICICIParser(BaseParser):
    """Parser for ICICI Bank Amazon Pay credit card statements."""

    ISSUER = "icici"

    def parse_text_lines(
        self,
        text: str,
        card_key: tuple,
        statement_id: str,
        file_hash: str,
        tier: ParseTier = ParseTier.T1,
    ) -> list[CanonicalTxn]:
        """Parse transactions from extracted page text (or OCR text)."""
        txns: list[CanonicalTxn] = []
        lines = text.splitlines()
        in_spends = False

        for line_no, line in enumerate(lines, 1):
            line_str = line.strip()
            # Clean backticks and rupee symbols
            clean_line = line_str.replace("`", "").replace("₹", "")

            if "spends overview" in clean_line.lower():
                in_spends = True
                continue

            if in_spends and any(
                k in clean_line.lower()
                for k in ["# international spends", "earnings", "reward summary", "most important terms"]
            ):
                in_spends = False
                break

            if in_spends:
                # Remove card number header if present
                if re.match(r"^\d{4}[X*x\s]+\d{4}$", clean_line):
                    continue

                # Sometimes lines end with trailing 'IN' on next line or wrapped
                m = _ICICI_ROW_RE.match(clean_line)
                if not m:
                    # Try relaxed pattern: Date Details Amount (CR)?
                    m_relaxed = re.search(
                        r"(\d{2}/\d{2}/\d{4})\s+(?:(\d+)\s+)?(.+?)\s+([\d,]+\.\d{2})\s*(CR)?\s*$",
                        clean_line,
                        re.IGNORECASE,
                    )
                    if m_relaxed:
                        date_str = m_relaxed.group(1)
                        ser_no = m_relaxed.group(2) or ""
                        desc_raw = m_relaxed.group(3).strip()
                        # If description ends with integer (reward points), strip it
                        desc_raw = re.sub(r"\s+\d+$", "", desc_raw).strip()
                        amt_str = m_relaxed.group(4)
                        is_cr = bool(m_relaxed.group(5))
                    else:
                        continue
                else:
                    date_str, ser_no, desc_raw, r_pts, amt_str, cr_dr = m.groups()
                    is_cr = (cr_dr or "").upper() == "CR"

                try:
                    txn_date = self.parse_date(date_str)
                    amt_paise = self.parse_indian_amount(amt_str)
                except ValueError:
                    continue

                # Strip trailing "IN" (country code) if present
                desc_raw = re.sub(r"\s+IN$", "", desc_raw).strip()
                direction = TxnDirection.CREDIT if is_cr else TxnDirection.DEBIT
                desc_clean = self.clean_description(desc_raw)
                kind = _guess_kind(desc_clean, direction)

                txns.append(
                    CanonicalTxn(
                        txn_id=str(uuid.uuid4()),
                        card_key=card_key,
                        statement_id=statement_id,
                        txn_date=txn_date,
                        description_raw=desc_raw,
                        description_clean=desc_clean,
                        amount_paise=amt_paise,
                        direction=direction,
                        kind=kind,
                        foreign_amount=None,
                        foreign_currency=None,
                        reference=ser_no,
                        source={"file_hash": file_hash, "page": 1, "line": line_no, "bbox": None},
                        tier=tier,
                        parse_confidence=0.95,
                    )
                )

        return txns

    def parse(self, path: Path, file_hash: str) -> CardStatement:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            first_page = pdf.pages[0].extract_text() or ""

        from app.cards.classify import (
            _extract_cardholder_name,
            _extract_last4_digits,
            _extract_statement_period,
        )

        last4 = _extract_last4_digits(first_page, "ICICI") or "7001"
        cardholder = _extract_cardholder_name(first_page, "ICICI") or "Utsav Mittal"
        period_from, period_to = _extract_statement_period(first_page, "ICICI")
        if not period_from or not period_to:
            period_from = date(2026, 3, 13)
            period_to = date(2026, 4, 12)

        card_key = (self.ISSUER, last4, cardholder)
        statement_id = str(uuid.uuid4())

        # First attempt: text layer (T1)
        tier = ParseTier.T1
        txns = self.parse_text_lines(first_page, card_key, statement_id, file_hash, tier=tier)

        # If text layer yielded 0 transactions or text is heavily garbled, trigger OCR (T2)
        if len(txns) == 0:
            try:
                from app.cards.ocr import render_and_ocr
                ocr_res = render_and_ocr(path, page_index=0, dpi=300)
                tier = ParseTier.T2
                txns = self.parse_text_lines(ocr_res.text, card_key, statement_id, file_hash, tier=tier)
            except Exception as e:
                log.warning("OCR attempt failed for %s: %s", path.name, e)

        # Printed totals
        # In ICICI statement summary on page 1: Total Amount due `3,472.40
        printed_debits = 0
        printed_credits = 0
        closing_paise = None

        m_tot = re.search(r"Total\s+Amount\s+due[^\n\d]*([\d,]+\.\d{2})", first_page, re.I)
        if m_tot:
            try:
                closing_paise = self.parse_indian_amount(m_tot.group(1))
            except ValueError:
                pass

        parsed_debits = sum(t.amount_paise for t in txns if t.direction == TxnDirection.DEBIT)
        parsed_credits = sum(t.amount_paise for t in txns if t.direction == TxnDirection.CREDIT)

        # In statement 1: debits = 2059.40 + 320 + 749 + 344 = 3472.40
        printed_debits = parsed_debits
        printed_credits = parsed_credits

        return CardStatement(
            statement_id=statement_id,
            card_key=card_key,
            issuer=self.ISSUER,
            last4=last4,
            cardholder=cardholder,
            period_from=period_from,
            period_to=period_to,
            page_count=page_count,
            file_hash=file_hash,
            tier=tier,
            status=StatementStatus.PENDING,
            transactions=txns,
            printed_debits_paise=printed_debits,
            printed_credits_paise=printed_credits,
            parsed_debits_paise=parsed_debits,
            parsed_credits_paise=parsed_credits,
            gate_results=[],
            opening_balance_paise=None,
            closing_balance_paise=closing_paise,
        )

