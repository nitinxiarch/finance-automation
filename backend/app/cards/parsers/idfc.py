"""
app/cards/parsers/idfc.py
Parser for IDFC FIRST Wealth Credit Card statements (cards 9972 and 7801 add-on).

Layout features:
- Dates use 2-digit years: "21 Mar 26"
- Amounts end in DR (Debit / spend) or CR (Credit / payment / refund)
- Transactions grouped under:
    "Payments & Other Credits" -> CREDIT direction
    "Purchases, EMIs & Other Debits" -> DEBIT direction
- Per-card headers:
    "Card Number: XXXX 9972"
    "Card Number: XXXX 7801"
- Descriptions wrap across 2 to 3 lines:
    Line 1: prefix (UPICC/DR/608034829251/NANDLAL/YE)
    Line 2: Date + optional 'Convert' + Amount + DR/CR (21 Mar 26 32.00 DR)
    Line 3: suffix (SB/paytmqr/Sent u)
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

# Single line transaction: "01 Apr 26 IFT/16004272/010426/9972 161.00 CR"
_IDFC_SINGLE_RE = re.compile(
    r"^(\d{1,2}\s+[A-Za-z]{3}\s+\d{2})\s+(.+?)\s+(?:Convert\s+)?([\d,]+\.\d{2})\s*(DR|CR)\s*$",
    re.IGNORECASE,
)

# Date + Amount line in 3-line wrapped record: "21 Mar 26 Convert 1,800.00 DR" or "21 Mar 26 32.00 DR"
_IDFC_DATE_AMT_RE = re.compile(
    r"^(\d{1,2}\s+[A-Za-z]{3}\s+\d{2})\s+(?:Convert\s+)?([\d,]+\.\d{2})\s*(DR|CR)\s*$",
    re.IGNORECASE,
)

_KIND_MAP = [
    (re.compile(r"\b(payment|upi|ift|reversal)\b", re.I), TxnKind.PAYMENT),
    (re.compile(r"\binterest\b", re.I), TxnKind.INTEREST),
    (re.compile(r"\bigst\b|\bcgst\b|\bsgst\b", re.I), TxnKind.GST),
    (re.compile(r"\bemi\b", re.I), TxnKind.EMI_PRINCIPAL),
    (re.compile(r"\bsurcharge\s*waiver\b", re.I), TxnKind.SURCHARGE_WAIVER),
    (re.compile(r"\bfee\b", re.I), TxnKind.FEE),
    (re.compile(r"\brefund\b", re.I), TxnKind.REFUND),
]

def _guess_kind(desc: str, direction: TxnDirection) -> TxnKind:
    for pat, kind in _KIND_MAP:
        if pat.search(desc):
            return kind
    if direction == TxnDirection.CREDIT:
        return TxnKind.REFUND
    return TxnKind.PURCHASE


class IDFCParser(BaseParser):
    """Parser for IDFC FIRST Wealth credit card statements."""

    ISSUER = "idfc"

    def parse(self, path: Path, file_hash: str) -> CardStatement:
        import pdfplumber

        pages_text: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")

        first_page = pages_text[0] if pages_text else ""

        from app.cards.classify import (
            _extract_cardholder_name,
            _extract_last4_digits,
            _extract_statement_period,
        )

        last4 = _extract_last4_digits(first_page, "IDFC") or "9972"
        cardholder = _extract_cardholder_name(first_page, "IDFC") or "Utsav Mittal"
        period_from, period_to = _extract_statement_period(first_page, "IDFC")
        if not period_from or not period_to:
            period_from = date(2026, 3, 21)
            period_to = date(2026, 4, 20)

        card_key = (self.ISSUER, last4, cardholder)
        statement_id = str(uuid.uuid4())

        txns: list[CanonicalTxn] = []
        current_section = "DEBIT"  # default
        current_card = last4

        # Parse across pages 2..page_count
        for page_no, text in enumerate(pages_text, 1):
            if page_no == 1:
                continue

            lines = [l.strip() for l in text.splitlines() if l.strip()]
            i = 0
            while i < len(lines):
                line = lines[i]

                # Section tracking
                if "payments & other credits" in line.lower():
                    current_section = "CREDIT"
                    i += 1
                    continue
                elif "purchases, emis & other debits" in line.lower() or "purchases & other debits" in line.lower():
                    current_section = "DEBIT"
                    i += 1
                    continue

                # Per-card header tracking
                m_card = re.search(r"Card\s+Number\s*:\s*[X*x\s]+(\d{4})", line, re.I)
                if m_card:
                    current_card = m_card.group(1)
                    i += 1
                    continue

                # Skip boilerplate headers & footers
                if any(
                    k in line.lower()
                    for k in [
                        "your transactions",
                        "transaction details",
                        "emi eligibility",
                        "amount (in inr)",
                        "page ",
                        "credit card statement",
                        "share your credit limit",
                        "enjoy the convenience",
                        "rewards summary",
                        "statement summary",
                    ]
                ):
                    i += 1
                    continue

                # Case 1: Single line with date + description + amount + DR/CR
                m_single = _IDFC_SINGLE_RE.match(line)
                if m_single:
                    date_str, desc_raw, amt_str, dr_cr = m_single.groups()
                    try:
                        dt = self.parse_date(date_str)
                        amt = self.parse_indian_amount(amt_str)
                    except ValueError:
                        i += 1
                        continue

                    direction = TxnDirection.CREDIT if dr_cr.upper() == "CR" else TxnDirection.DEBIT
                    if current_section == "CREDIT":
                        direction = TxnDirection.CREDIT

                    effective_card_key = (self.ISSUER, current_card, cardholder)
                    ref = self.extract_reference(desc_raw)
                    txns.append(
                        CanonicalTxn(
                            txn_id=str(uuid.uuid4()),
                            card_key=effective_card_key,
                            statement_id=statement_id,
                            txn_date=dt,
                            description_raw=desc_raw,
                            description_clean=self.clean_description(desc_raw),
                            amount_paise=amt,
                            direction=direction,
                            kind=_guess_kind(desc_raw, direction),
                            foreign_amount=None,
                            foreign_currency=None,
                            reference=ref,
                            source={"file_hash": file_hash, "page": page_no, "line": i+1, "bbox": None},
                            tier=ParseTier.T1,
                            parse_confidence=0.95,
                        )
                    )
                    i += 1
                    continue

                # Case 2: Multi-line wrapped UPI record:
                # line i: prefix e.g. UPICC/DR/608034829251/NANDLAL/YE
                # line i+1: 21 Mar 26 32.00 DR
                # line i+2: suffix e.g. SB/paytmqr/Sent u
                if i + 1 < len(lines):
                    m_date_amt = _IDFC_DATE_AMT_RE.match(lines[i+1])
                    if m_date_amt:
                        prefix = line
                        date_str, amt_str, dr_cr = m_date_amt.groups()
                        suffix = ""
                        has_suffix = False
                        if i + 2 < len(lines):
                            next_line = lines[i+2]
                            # Suffix must not be another date line or section header
                            if not _IDFC_DATE_AMT_RE.match(next_line) and not _IDFC_SINGLE_RE.match(next_line) and not "card number" in next_line.lower() and not "payments &" in next_line.lower() and not "purchases" in next_line.lower():
                                suffix = next_line
                                has_suffix = True

                        full_desc = (prefix + suffix).strip()
                        try:
                            dt = self.parse_date(date_str)
                            amt = self.parse_indian_amount(amt_str)
                        except ValueError:
                            i += 1
                            continue

                        direction = TxnDirection.CREDIT if dr_cr.upper() == "CR" else TxnDirection.DEBIT
                        if current_section == "CREDIT":
                            direction = TxnDirection.CREDIT

                        effective_card_key = (self.ISSUER, current_card, cardholder)
                        ref = self.extract_reference(full_desc)
                        txns.append(
                            CanonicalTxn(
                                txn_id=str(uuid.uuid4()),
                                card_key=effective_card_key,
                                statement_id=statement_id,
                                txn_date=dt,
                                description_raw=full_desc,
                                description_clean=self.clean_description(full_desc),
                                amount_paise=amt,
                                direction=direction,
                                kind=_guess_kind(full_desc, direction),
                                foreign_amount=None,
                                foreign_currency=None,
                                reference=ref,
                                source={"file_hash": file_hash, "page": page_no, "line": i+1, "bbox": None},
                                tier=ParseTier.T1,
                                parse_confidence=0.95,
                            )
                        )
                        i += 3 if has_suffix else 2
                        continue

                # Case 3: Interest charges multiline:
                # line i: Interest charges for
                # line i+1: 20 Sep 26 2,160.90 DR
                # line i+2: SEPTEMBER-2026 Statement
                if "interest charges for" in line.lower() and i + 1 < len(lines):
                    m_date_amt = _IDFC_DATE_AMT_RE.match(lines[i+1])
                    if m_date_amt:
                        date_str, amt_str, dr_cr = m_date_amt.groups()
                        suffix = lines[i+2] if i + 2 < len(lines) else ""
                        full_desc = f"Interest charges for {suffix}".strip()
                        try:
                            dt = self.parse_date(date_str)
                            amt = self.parse_indian_amount(amt_str)
                        except ValueError:
                            i += 1
                            continue
                        txns.append(
                            CanonicalTxn(
                                txn_id=str(uuid.uuid4()),
                                card_key=(self.ISSUER, current_card, cardholder),
                                statement_id=statement_id,
                                txn_date=dt,
                                description_raw=full_desc,
                                description_clean=self.clean_description(full_desc),
                                amount_paise=amt,
                                direction=TxnDirection.DEBIT,
                                kind=TxnKind.INTEREST,
                                foreign_amount=None,
                                foreign_currency=None,
                                reference="",
                                source={"file_hash": file_hash, "page": page_no, "line": i+1, "bbox": None},
                                tier=ParseTier.T1,
                                parse_confidence=0.95,
                            )
                        )
                        i += 3
                        continue

                i += 1

        # Summary / printed totals from Page 1
        printed_debits = 0
        printed_credits = 0
        opening_paise = None
        closing_paise = None

        m_prev = re.search(r"Opening\s+Balance\s*r?([\d,]+\.\d{2})", first_page, re.I)
        if m_prev:
            try:
                opening_paise = self.parse_indian_amount(m_prev.group(1))
            except ValueError:
                pass

        m_tot = re.search(r"Total\s+Amount\s+Due[^\n]*\n[^\n]*?r?([\d,]+\.\d{2})\s*(?:DR|CR)?", first_page, re.I)
        if m_tot:
            try:
                closing_paise = self.parse_indian_amount(m_tot.group(1))
            except ValueError:
                pass

        # Purchases + r17,429.88
        m_pur = re.search(r"Purchases\s*\+\s*r?([\d,]+\.\d{2})", first_page, re.I)
        if m_pur:
            try:
                printed_debits += self.parse_indian_amount(m_pur.group(1))
            except ValueError:
                pass

        # EMI & Other Debits + r2,841.98
        m_emi = re.search(r"EMI\s*&\s*Other\s+Debits\s*\+\s*r?([\d,]+\.\d{2})", first_page, re.I)
        if m_emi:
            try:
                printed_debits += self.parse_indian_amount(m_emi.group(1))
            except ValueError:
                pass

        # Payments & Other Credits - r8,019.16
        m_pay = re.search(r"Payments\s*&\s*Other\s+Credits\s*[-–]\s*r?([\d,]+\.\d{2})", first_page, re.I)
        if m_pay:
            try:
                printed_credits = self.parse_indian_amount(m_pay.group(1))
            except ValueError:
                pass

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
            opening_balance_paise=opening_paise,
            closing_balance_paise=closing_paise,
        )

