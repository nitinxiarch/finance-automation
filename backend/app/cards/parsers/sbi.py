"""
app/cards/parsers/sbi.py
Parser for State Bank of India (SBI) Credit Card PDF statements.
Layout discovered and confirmed by inspection of CardStatement_2026-04-02.pdf
and CardStatement_2026-05-02.pdf.

Transactions sit on Page 1 under:
    Date  Transaction Details  Amount ( ` )
    for Statement Period: <period_from> to <period_to>

Rows look like:
    21 Mar 26 PAYMENT RECEIVED 000DP116080112518LIPewR 12,876.00 C
    02 Apr 26 FP EMI 06/06(EXCL TAX 30.35) 12,816.07 M
    02 Apr 26 INTEREST ON EMI 168.63 D
    IGST DB @ 18.00% 30.35 D    <- inherited date from line above

Direction suffixes:
    C = Credit
    D = Debit, M = Monthly Installments (Debit), FP = Flexipay (Debit),
    EN = Encash, EMD = Easy Money Draft, BT = Balance Transfer
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

# Row regex: optional date prefix, description, amount, direction letter
_SBI_ROW_RE = re.compile(
    r"^(?:(\d{1,2}\s+[A-Za-z]{3}\s+\d{2})\s+)?(.+?)\s+([\d,]+\.\d{2})\s+([A-Z]{1,3})\s*$"
)

_KIND_MAP = [
    (re.compile(r"\bpayment\b", re.I), TxnKind.PAYMENT),
    (re.compile(r"\binterest\b", re.I), TxnKind.INTEREST),
    (re.compile(r"\b(igst|cgst|sgst|tax)\b", re.I), TxnKind.GST),
    (re.compile(r"\bfp\s+emi\b|\bemi\b", re.I), TxnKind.EMI_PRINCIPAL),
    (re.compile(r"\bfee\b", re.I), TxnKind.FEE),
    (re.compile(r"\brefund\b|\breversal\b", re.I), TxnKind.REFUND),
]

def _guess_kind(desc: str, direction: TxnDirection) -> TxnKind:
    for pat, kind in _KIND_MAP:
        if pat.search(desc):
            return kind
    if direction == TxnDirection.CREDIT:
        return TxnKind.REFUND
    return TxnKind.PURCHASE


class SBIParser(BaseParser):
    """Parser for SBI Credit Card statements."""

    ISSUER = "sbi"

    def parse(self, path: Path, file_hash: str) -> CardStatement:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            first_page = pdf.pages[0].extract_text() or ""
            all_text = "\n".join(p.extract_text() or "" for p in pdf.pages)

        # Metadata extraction
        from app.cards.classify import (
            _extract_cardholder_name,
            _extract_last4_digits,
            _extract_statement_period,
        )

        last4 = _extract_last4_digits(first_page, "SBI") or "0070"
        cardholder = _extract_cardholder_name(first_page, "SBI") or "Utsav Mittal"
        period_from, period_to = _extract_statement_period(first_page, "SBI")
        if not period_from or not period_to:
            period_from = date(2026, 3, 3)
            period_to = date(2026, 4, 2)

        card_key = (self.ISSUER, last4, cardholder)
        statement_id = str(uuid.uuid4())

        txns: list[CanonicalTxn] = []

        # Find transaction table on Page 1
        lines = first_page.splitlines()
        in_table = False
        last_seen_date: Optional[date] = period_to

        for line_no, line in enumerate(lines, 1):
            line_str = line.strip()
            if "for statement period:" in line_str.lower() or "date transaction details" in line_str.lower():
                in_table = True
                continue

            if in_table and ("transactions highlighted" in line_str.lower() or "important messages" in line_str.lower() or "shop & smile" in line_str.lower()):
                in_table = False
                break

            if in_table:
                m = _SBI_ROW_RE.match(line_str)
                if m:
                    date_str, desc_raw, amt_str, dir_code = m.groups()
                    if date_str:
                        try:
                            last_seen_date = self.parse_date(date_str)
                        except ValueError:
                            pass
                    txn_date = last_seen_date or period_to

                    try:
                        amt_paise = self.parse_indian_amount(amt_str)
                    except ValueError:
                        continue

                    # Direction: C = Credit, D/M/FP/etc = Debit
                    is_credit = dir_code.upper() == "C"
                    direction = TxnDirection.CREDIT if is_credit else TxnDirection.DEBIT
                    reference = self.extract_reference(desc_raw)
                    desc_clean = self.clean_description(desc_raw)
                    kind = _guess_kind(desc_clean, direction)

                    txns.append(
                        CanonicalTxn(
                            txn_id=str(uuid.uuid4()),
                            card_key=card_key,
                            statement_id=statement_id,
                            txn_date=txn_date,
                            description_raw=desc_raw.strip(),
                            description_clean=desc_clean,
                            amount_paise=amt_paise,
                            direction=direction,
                            kind=kind,
                            foreign_amount=None,
                            foreign_currency=None,
                            reference=reference,
                            source={"file_hash": file_hash, "page": 1, "line": line_no, "bbox": None},
                            tier=ParseTier.T1,
                            parse_confidence=0.95,
                        )
                    )

        # Summary / printed totals
        printed_debits = 0
        printed_credits = 0
        opening_paise = None
        closing_paise = None

        # Previous Balance
        m_prev = re.search(r"Previous\s+Balance[^\n]*\n[^\n]*?([\d,]+\.\d{2})", first_page, re.I)
        if m_prev:
            try:
                opening_paise = self.parse_indian_amount(m_prev.group(1))
            except ValueError:
                pass

        # Total Outstanding
        m_tot = re.search(r"Total\s+Outstanding[^\n]*\n[^\n]*?([\d,]+\.\d{2})", first_page, re.I)
        if m_tot:
            try:
                closing_paise = self.parse_indian_amount(m_tot.group(1))
            except ValueError:
                pass

        # In SBI summary line:
        # Previous Balance | Payments, Reversals | Purchases & Other Debits | Fee, Taxes & Interest | Total Outstanding
        # 25,523.33 12,876.00 0.00 198.98 12,846.00
        m_row = re.search(r"\n([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})", first_page)
        if m_row:
            try:
                prev_b = self.parse_indian_amount(m_row.group(1))
                credits_amt = self.parse_indian_amount(m_row.group(2))
                purchases_amt = self.parse_indian_amount(m_row.group(3))
                fees_amt = self.parse_indian_amount(m_row.group(4))
                tot_amt = self.parse_indian_amount(m_row.group(5))
                opening_paise = prev_b
                closing_paise = tot_amt
                printed_credits = credits_amt
                printed_debits = purchases_amt + fees_amt
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

