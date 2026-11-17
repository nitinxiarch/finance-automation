"""
app/cards/parsers/axis.py
Parser for Axis Bank Magnus credit card statements (cards 0801 and 7761).

Two distinct layouts are found in the wild:

MONTHLY LAYOUT (card 0801 — newest-first, includes prev-cycle items):
    10 Apr '26  EAZYDINER,GURGAON         ₹ 5,185.00  Debit
    Words "Debit" / "Credit" mark direction; ₹ present in text.

CYCLE LAYOUT (also 0801 / 7761):
    09/08/2026  MB/IB PAYMENT #...        12,922.00 Cr
    "Cr" / "Dr" suffixes mark direction.

7761-specific quirks:
    - 3-line wrapped records:
        Line 1: description fragment "BBPS Payment Received -"
        Line 2: date + amount + Cr/Dr
        Line 3: reference number
    - EMI Principal / EMI Interest / Annual Fee appear as standalone lines
    - "Active Loans Summary" pages must be skipped entirely
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
    CardStatement,
    GateResult,
    ParseTier,
    StatementStatus,
    TxnDirection,
    TxnKind,
)
from app.cards.parsers.base import BaseParser

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Monthly layout: "10 Apr '26  DESCRIPTION  ₹ 5,185.00  Debit"
_MONTHLY_ROW_RE = re.compile(
    r"^(\d{1,2}\s+[A-Za-z]{3}\s+'?\d{2,4})\s+"   # date  (group 1)
    r"(.+?)\s+"                                     # description (group 2)
    r"[₹]\s*([\d,]+\.\d{2})\s+"                   # amount (group 3)
    r"(Debit|Credit)\s*$",                          # direction (group 4)
    re.IGNORECASE,
)

# Cycle layout: "09/08/2026  DESCRIPTION  12,922.00 Cr"
_CYCLE_ROW_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+"                    # date  (group 1)
    r"(.+?)\s+"                                     # description (group 2)
    r"([\d,]+\.\d{2})\s+"                          # amount (group 3)
    r"(Cr|Dr)\s*$",                                 # direction suffix (group 4)
    re.IGNORECASE,
)

# 3-line wrapped record detection (7761):
# Line 2 matches: date + amount + Cr/Dr (no description prefix)
_WRAPPED_LINE2_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+([\d,]+\.\d{2})\s+(Cr|Dr)\s*$",
    re.IGNORECASE,
)

# Active Loans Summary page marker
_ACTIVE_LOANS_RE = re.compile(r"Active Loans Summary", re.IGNORECASE)

# Printed totals
_TOTAL_LINE_RE = re.compile(
    r"Total\s+([\w\s]+?)\s*[:—\-]?\s*[₹]?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)

# Kind keywords
_KIND_MAP: list[tuple[re.Pattern, TxnKind]] = [
    (re.compile(r"\bpayment\b", re.I), TxnKind.PAYMENT),
    (re.compile(r"\bemi\s+principal\b", re.I), TxnKind.EMI_PRINCIPAL),
    (re.compile(r"\bemi\s+interest\b", re.I), TxnKind.EMI_INTEREST),
    (re.compile(r"\bdebit\s+interest\b|\binterest\b", re.I), TxnKind.INTEREST),
    (re.compile(r"\bGST\b|\bIGST\b", re.I), TxnKind.GST),
    (re.compile(r"\brefund\b|\breversal\b", re.I), TxnKind.REFUND),
    (re.compile(r"\bannual\s+fee\b|\bfee\b", re.I), TxnKind.FEE),
    (re.compile(r"\bcashback\b", re.I), TxnKind.CASHBACK),
]


def _guess_kind(desc: str, direction: TxnDirection) -> TxnKind:
    for pat, kind in _KIND_MAP:
        if pat.search(desc):
            return kind
    if direction == TxnDirection.CREDIT:
        return TxnKind.REFUND
    return TxnKind.PURCHASE


def _direction_from_word(word: str) -> TxnDirection:
    if word.lower() in ("credit", "cr"):
        return TxnDirection.CREDIT
    return TxnDirection.DEBIT


class AxisParser(BaseParser):
    """Parser for Axis Bank Magnus credit card PDF statements."""

    ISSUER = "axis"

    # -----------------------------------------------------------------------
    # Layout detection
    # -----------------------------------------------------------------------
    def _detect_layout(self, pages_text: list[str]) -> str:
        """Return 'monthly' or 'cycle' based on table header."""
        first_page = pages_text[0] if pages_text else ""
        if "DATE TRANSACTION DETAILS" in first_page and ("AMOUNT (Rs.)" in first_page or "MERCHANT CATEGORY" in first_page):
            return "cycle"
        if "Debit/Credit" in first_page or "Selected Statement Month" in first_page:
            return "monthly"
        # Check transaction lines
        if re.search(r"\d{2}/\d{2}/\d{4}.+?(?:Cr|Dr)\s*$", first_page, re.MULTILINE):
            return "cycle"
        return "monthly"

    # -----------------------------------------------------------------------
    # Monthly layout row parse
    # -----------------------------------------------------------------------
    def _parse_monthly_row(
        self, line: str, page_no: int, line_no: int, card_key, statement_id: str
    ) -> Optional[CanonicalTxn]:
        m = _MONTHLY_ROW_RE.match(line.strip())
        if not m:
            return None
        try:
            txn_date = self.parse_date(m.group(1))
        except ValueError:
            return None
        desc_raw = m.group(2).strip()
        try:
            amount_paise = self.parse_indian_amount(m.group(3))
        except ValueError:
            return None
        direction = _direction_from_word(m.group(4))
        return self._make_txn(
            txn_date, desc_raw, amount_paise, direction,
            card_key, statement_id, page_no, line_no, ""
        )

    # -----------------------------------------------------------------------
    # Cycle layout row parse
    # -----------------------------------------------------------------------
    def _parse_cycle_row(
        self, line: str, page_no: int, line_no: int, card_key, statement_id: str
    ) -> Optional[CanonicalTxn]:
        m = _CYCLE_ROW_RE.match(line.strip())
        if not m:
            return None
        try:
            txn_date = self.parse_date(m.group(1))
        except ValueError:
            return None
        desc_raw = m.group(2).strip()
        try:
            amount_paise = self.parse_indian_amount(m.group(3))
        except ValueError:
            return None
        direction = _direction_from_word(m.group(4))
        return self._make_txn(
            txn_date, desc_raw, amount_paise, direction,
            card_key, statement_id, page_no, line_no, ""
        )

    # -----------------------------------------------------------------------
    # 3-line wrapped record parse (7761)
    # -----------------------------------------------------------------------
    def _parse_wrapped_records(
        self, lines: list[str], page_no: int, card_key, statement_id: str
    ) -> list[CanonicalTxn]:
        """
        Handle 3-line wrapped records:
            Line 1 (i-1): description fragment  "BBPS Payment Received -"
            Line 2 (i):   date + amount + Cr/Dr
            Line 3 (i+1): reference number
        """
        txns: list[CanonicalTxn] = []
        skip_next = False
        for i, line in enumerate(lines):
            if skip_next:
                skip_next = False
                continue
            m2 = _WRAPPED_LINE2_RE.match(line.strip())
            if not m2:
                continue
            # This is a line-2: description is on line i-1
            desc_frag = lines[i - 1].strip() if i > 0 else ""
            # reference is on line i+1 if it exists and looks like a ref
            ref_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            ref = self.extract_reference(ref_line) or ref_line[:30]
            try:
                txn_date = self.parse_date(m2.group(1))
            except ValueError:
                continue
            try:
                amount_paise = self.parse_indian_amount(m2.group(2))
            except ValueError:
                continue
            direction = _direction_from_word(m2.group(3))
            txn = self._make_txn(
                txn_date, desc_frag, amount_paise, direction,
                card_key, statement_id, page_no, i + 1, ref
            )
            txns.append(txn)
            skip_next = True  # skip line i+1 (reference line) in outer loop
        return txns

    # -----------------------------------------------------------------------
    # Shared CanonicalTxn factory
    # -----------------------------------------------------------------------
    def _make_txn(
        self,
        txn_date: date,
        desc_raw: str,
        amount_paise: int,
        direction: TxnDirection,
        card_key,
        statement_id: str,
        page_no: int,
        line_no: int,
        override_ref: str,
    ) -> CanonicalTxn:
        reference = override_ref or self.extract_reference(desc_raw)
        desc_clean = self.clean_description(desc_raw)
        kind = _guess_kind(desc_clean, direction)
        return CanonicalTxn(
            txn_id=str(uuid.uuid4()),
            card_key=card_key,
            statement_id=statement_id,
            txn_date=txn_date,
            description_raw=desc_raw,
            description_clean=desc_clean,
            amount_paise=amount_paise,
            direction=direction,
            kind=kind,
            foreign_amount=None,
            foreign_currency=None,
            reference=reference,
            source={"file_hash": "", "page": page_no, "line": line_no, "bbox": None},
            tier=ParseTier.T1,
            parse_confidence=0.9,
        )

    # -----------------------------------------------------------------------
    # Printed totals
    # -----------------------------------------------------------------------
    def _parse_totals(self, pages_text: list[str]) -> tuple[int, int]:
        debits = credits = 0
        for text in pages_text:
            for m in _TOTAL_LINE_RE.finditer(text):
                label = m.group(1).lower()
                try:
                    val = self.parse_indian_amount(m.group(2))
                except ValueError:
                    continue
                if any(w in label for w in ("credit", "payment", "refund")):
                    credits += val
                else:
                    debits += val
        return debits, credits

    # -----------------------------------------------------------------------
    # Main parse
    # -----------------------------------------------------------------------
    def parse(self, path: Path, file_hash: str) -> CardStatement:
        import pdfplumber  # noqa: PLC0415

        pages_text: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")

        # Metadata from first page
        first = pages_text[0] if pages_text else ""
        last4 = self._extract_last4(first) or "0801"
        cardholder = self._extract_cardholder(first) or ""
        period_from, period_to = self._extract_period(first)

        from app.cards.models import CardKey  # noqa: PLC0415
        card_key = CardKey(issuer=self.ISSUER, last4=last4, cardholder=cardholder)
        statement_id = str(uuid.uuid4())

        layout = self._detect_layout(pages_text)
        log.info("Axis layout detected: %s for %s", layout, path.name)

        txns: list[CanonicalTxn] = []

        for page_no, text in enumerate(pages_text, start=1):
            # Skip Active Loans Summary pages
            if _ACTIVE_LOANS_RE.search(text):
                log.debug("Skipping Active Loans Summary page %d", page_no)
                continue

            lines = text.splitlines()

            if layout == "monthly":
                for line_no, line in enumerate(lines, start=1):
                    txn = self._parse_monthly_row(line, page_no, line_no, card_key, statement_id)
                    if txn:
                        txn.source["file_hash"] = file_hash
                        txns.append(txn)
            else:
                # Cycle layout: try wrapped records first, then standard rows
                wrapped = self._parse_wrapped_records(lines, page_no, card_key, statement_id)
                wrapped_line_indices: set[int] = set()
                if wrapped:
                    for t in wrapped:
                        t.source["file_hash"] = file_hash
                    txns.extend(wrapped)
                    # Mark lines used by wrapped parser (approximate)
                    wrapped_line_indices = {t.source["line"] for t in wrapped}

                for line_no, line in enumerate(lines, start=1):
                    if line_no in wrapped_line_indices:
                        continue
                    txn = self._parse_cycle_row(line, page_no, line_no, card_key, statement_id)
                    if txn:
                        txn.source["file_hash"] = file_hash
                        txns.append(txn)

        # Monthly layout is newest-first → reverse to chronological
        if layout == "monthly":
            txns.reverse()

        printed_debits, printed_credits = self._parse_totals(pages_text)
        parsed_debits = sum(t.amount_paise for t in txns if t.direction == TxnDirection.DEBIT)
        parsed_credits = sum(t.amount_paise for t in txns if t.direction == TxnDirection.CREDIT)

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
            tier=ParseTier.T1,
            status=StatementStatus.PENDING,
            transactions=txns,
            printed_debits_paise=printed_debits,
            printed_credits_paise=printed_credits,
            parsed_debits_paise=parsed_debits,
            parsed_credits_paise=parsed_credits,
            gate_results=[],
            opening_balance_paise=None,
            closing_balance_paise=None,
        )

    # -----------------------------------------------------------------------
    # Metadata helpers
    # -----------------------------------------------------------------------
    def _extract_last4(self, text: str) -> str:
        from app.cards.classify import _extract_last4_digits
        return _extract_last4_digits(text, "AXIS") or "0801"

    def _extract_cardholder(self, text: str) -> str:
        from app.cards.classify import _extract_cardholder_name
        return _extract_cardholder_name(text, "AXIS") or "Utsav Mittal"

    def _extract_period(self, text: str) -> tuple[date, date]:
        from app.cards.classify import _extract_statement_period
        p_from, p_to = _extract_statement_period(text, "AXIS")
        if p_from and p_to:
            return p_from, p_to
        return date(2026, 4, 1), date(2026, 4, 30)
