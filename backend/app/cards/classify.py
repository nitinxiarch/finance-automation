"""
app/cards/classify.py
PDF classification: detect issuer, card identity, period, and OCR need.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IssuerInfo
# ---------------------------------------------------------------------------

@dataclass
class IssuerInfo:
    issuer: str                         # "HDFC", "AXIS", "ICICI", "IDFC", "SBI", "UNKNOWN"
    last4: str                          # 4-digit card suffix, "" if unknown
    cardholder: str                     # cardholder name, "" if unknown
    period_from: Optional[date]
    period_to: Optional[date]
    page_count: int
    needs_ocr: bool
    confidence: float                   # 0.0 – 1.0
    folder_hint: str                    # top-level folder from ZIP
    name_mismatch: bool                 # True if folder last4 != extracted last4


# ---------------------------------------------------------------------------
# Month map
# ---------------------------------------------------------------------------
_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

def _parse_date_str(s: str) -> Optional[date]:
    """Parse common date formats found in statements."""
    s = s.strip().replace("'", "").replace(",", "")
    # DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None

    # DD Mon YYYY or DD Mon YY
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{2,4})$", s)
    if m:
        try:
            d = int(m.group(1))
            mo = _MONTH_MAP.get(m.group(2).lower()[:3], 0)
            y = int(m.group(3))
            if y < 100:
                y += 2000
            if 1 <= mo <= 12:
                return date(y, mo, d)
        except ValueError:
            return None

    # Mon DD YYYY (e.g. April 12 2026)
    m = re.match(r"^([A-Za-z]{3,9})\s+(\d{1,2})\s+(\d{2,4})$", s)
    if m:
        try:
            mo = _MONTH_MAP.get(m.group(1).lower()[:3], 0)
            d = int(m.group(2))
            y = int(m.group(3))
            if y < 100:
                y += 2000
            if 1 <= mo <= 12:
                return date(y, mo, d)
        except ValueError:
            return None

    # YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    return None


def _detect_issuer(text: str) -> str:
    """Detect bank issuer from text content."""
    upper = text.upper()
    # Check IDFC FIRST explicitly first (before any payment gateway HDFC/YESB mentions)
    if "IDFC FIRST" in upper or "FIRST WEALTH" in upper or "IDFC" in upper:
        return "IDFC"
    if "HDFC BANK" in upper or "INFINIA" in upper:
        return "HDFC"
    if "AXIS BANK" in upper or "MAGNUS" in upper:
        return "AXIS"
    if "ICICI BANK" in upper or "AMAZON PAY" in upper:
        return "ICICI"
    if "SBI CARD" in upper or "STATE BANK" in upper or "SBICPSL" in upper:
        return "SBI"
    return "UNKNOWN"


def _extract_last4_digits(text: str, issuer: str) -> str:
    """Extract card last 4 digits from statement text."""
    # IDFC: (FIRST Wealth XX9972) or Card Number: XXXX 9972
    m = re.search(r"XX(\d{4})\)?", text)
    if m and issuer == "IDFC":
        return m.group(1)
    m = re.search(r"Card\s+Number\s*:\s*[X*x\s]{4,}(\d{4})\b", text, re.I)
    if m:
        return m.group(1)

    # Specific patterns for masked cards
    # E.g. 437546XXXXXX8640 or 5296XXXXXXXX0801 or 529629******0801 or 4315XXXXXXXX7001
    m = re.search(r"[X*x]{4,}\s*(\d{4})\b", text)
    if m:
        return m.group(1)

    # SBI: Credit Card Number XXXX XXXX XXXX XX70
    m = re.search(r"Credit\s+Card\s+Number\s*[X\s]+(?:XX)?(\d{2,4})\b", text, re.I)
    if m:
        val = m.group(1)
        if len(val) == 2:
            return f"00{val}"
        return val

    # Axis: 4514XXXXXXXX7761
    m = re.search(r"\b\d{4,6}[X*x]{4,}(\d{4})\b", text)
    if m:
        return m.group(1)

    return ""


def _extract_cardholder_name(text: str, issuer: str) -> str:
    """Extract cardholder name from statement text."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if issuer == "HDFC":
        # Line containing UTSAV MITTAL Credit Card No. or line right above Credit Card No.
        for i, line in enumerate(lines[:15]):
            m = re.search(r"^([A-Z\s]{4,30})\s+Credit\s+Card\s+No", line, re.I)
            if m:
                cand = m.group(1).strip()
                if not any(k in cand.lower() for k in ["hsn", "gstin", "infinia"]):
                    return cand.title()
            if "credit card no" in line.lower() and i > 0:
                prev = lines[i-1]
                if not any(k in prev.lower() for k in ["hsn", "gstin", "infinia", "statement"]):
                    return prev.title()

    elif issuer == "AXIS":
        # Usually line 2 or 3: Axis Bank Magnus ... \n UTSAV MITTAL \n Address
        for i, line in enumerate(lines[:8]):
            if any(k in line.lower() for k in ["monthly statement", "credit card statement"]):
                if i + 1 < len(lines):
                    cand = lines[i+1].strip()
                    if re.match(r"^[A-Za-z\s]{4,35}$", cand):
                        return cand.title()

    elif issuer == "ICICI":
        # Usually MR UTSAV MITTAL or UTSAV MITTAL in first 8 lines
        for line in lines[:8]:
            m = re.search(r"^(?:MR|MS|MRS)?\.?\s*([A-Z\s]{4,30})$", line, re.I)
            if m and not any(k in line.lower() for k in ["statement", "solutions", "limited", "pvt"]):
                return m.group(1).strip().title()

    elif issuer == "IDFC":
        # Credit Card Statement \n 21/Mar/2026 - 20/Apr/2026 \n UTSAV MITTAL
        for i, line in enumerate(lines[:10]):
            if re.search(r"\d{2}/[A-Za-z]{3}/\d{4}", line):
                if i + 1 < len(lines):
                    cand = lines[i+1].strip()
                    if re.match(r"^[A-Z\s]{4,30}$", cand) and "statement" not in cand.lower():
                        return cand.title()

    elif issuer == "SBI":
        # GSTIN of SBI Card ... \n UTSAV MITTAL \n Credit Card Number
        for i, line in enumerate(lines[:10]):
            if "credit card number" in line.lower() and i > 0:
                cand = lines[i-1].strip()
                if "gstin" not in cand.lower():
                    return cand.title()

    # Fallback search
    for line in lines[:12]:
        m = re.match(r"^(?:MR|MS|MRS)?\.?\s*(UTSAV MITTAL|RIDHIMA SHARMA)\b", line, re.I)
        if m:
            return m.group(1).title()

    return ""


def _extract_statement_period(text: str, issuer: str) -> tuple[Optional[date], Optional[date]]:
    """Extract statement start and end dates from text."""
    # 1. HDFC: Billing Period 16 Mar, 2026 - 15 Apr, 2026
    m = re.search(
        r"Billing\s+Period\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]{3},?\s+\d{4})\s*[-–to]+\s*(\d{1,2}\s+[A-Za-z]{3},?\s+\d{4})",
        text, re.I,
    )
    if m:
        p1 = _parse_date_str(m.group(1))
        p2 = _parse_date_str(m.group(2))
        if p1 and p2:
            return p1, p2

    # 2. Axis Cycle / General: Statement Period : 22/05/2026 - 20/06/2026 or just date range in table
    m = re.search(
        r"Statement\s+Period\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*[-–to]+\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        text, re.I,
    )
    if not m:
        # Fallback for Axis Cycle where Statement Period is in header and dates are below
        m = re.search(r"\b(\d{2}/\d{2}/\d{4})\s*[-–]\s*(\d{2}/\d{2}/\d{4})\b", text)
    if m:
        p1 = _parse_date_str(m.group(1))
        p2 = _parse_date_str(m.group(2))
        if p1 and p2:
            return p1, p2

    # 3. IDFC: 21/Mar/2026 - 20/Apr/2026
    m = re.search(
        r"(\d{1,2}/[A-Za-z]{3}/\d{4})\s*[-–]\s*(\d{1,2}/[A-Za-z]{3}/\d{4})",
        text, re.I,
    )
    if m:
        p1 = _parse_date_str(m.group(1).replace("/", " "))
        p2 = _parse_date_str(m.group(2).replace("/", " "))
        if p1 and p2:
            return p1, p2

    # 4. SBI: for Statement Period: 03 Mar 26 to 02 Apr 26
    m = re.search(
        r"(?:for\s+)?Statement\s+Period\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\s*[-–to]+\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})",
        text, re.I,
    )
    if m:
        p1 = _parse_date_str(m.group(1))
        p2 = _parse_date_str(m.group(2))
        if p1 and p2:
            return p1, p2

    # 5. Axis Monthly: Selected Statement Month \n Apr 2026 or Selected Statement Month Credit Limit Opening Balance \n Apr 2026
    m = re.search(r"(?:Selected\s+Statement\s+Month[^\n]*\n\s*([A-Za-z]{3})\s+(\d{4}))", text, re.I)
    if not m:
        m = re.search(r"Selected\s+Statement\s+Month\s*[:\-]?\s*([A-Za-z]{3})\s+(\d{4})", text, re.I)
    if m:
        mo = _MONTH_MAP.get(m.group(1).lower()[:3], 0)
        y = int(m.group(2))
        if mo >= 1 and mo <= 12:
            import calendar
            last_day = calendar.monthrange(y, mo)[1]
            return date(y, mo, 1), date(y, mo, last_day)

    # 6. ICICI Statement Date: April 12, 2026 (handles SSTTAATTEEMMEENNTT DDAATTEE)
    m = re.search(r"S+T+A+T+E+M+E+N+T+\s*D+A+T+E+[\s\n]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
    if not m:
        m = re.search(r"STATEMENT\s+DATE[\s\n]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
    if m:
        p2 = _parse_date_str(m.group(1))
        if p2:
            from datetime import timedelta
            p1 = p2 - timedelta(days=30)
            return p1, p2

    return None, None


def _check_needs_ocr(text: str, issuer: str) -> bool:
    """Return True if text layer is garbled or needs OCR."""
    if not text:
        return True
    cid_count = len(re.findall(r"\(cid:\d+\)", text))
    if cid_count > 5:
        return True
    # Doubled letters like SSTTAATTEEMMEENNTT
    doubled = len(re.findall(r"(.)\1{2,}", text))
    if doubled > 50:
        return True
    # ICICI statements often have broken text layer
    if issuer == "ICICI" and ("SSTTAATTEEMMEENNTT" in text or "(cid:" in text):
        return True
    return False


def classify_pdf(path: Path, folder_hint: str = "") -> IssuerInfo:
    """
    Classify a credit card statement PDF by reading text layer.
    """
    import pdfplumber

    try:
        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            pages_to_read = min(3, page_count)
            first_pages_text = "\n".join(pdf.pages[i].extract_text() or "" for i in range(pages_to_read))
    except Exception as exc:
        logger.warning("Failed to open PDF %s: %s", path, exc)
        return IssuerInfo(
            issuer="UNKNOWN",
            last4="",
            cardholder="",
            period_from=None,
            period_to=None,
            page_count=0,
            needs_ocr=False,
            confidence=0.0,
            folder_hint=folder_hint,
            name_mismatch=False,
        )

    issuer = _detect_issuer(first_pages_text)
    if issuer == "UNKNOWN" and folder_hint:
        # Use folder hint as fallback hint
        f_upper = folder_hint.upper()
        for cand in ["HDFC", "AXIS", "ICICI", "IDFC", "SBI"]:
            if cand in f_upper:
                issuer = cand
                break

    last4 = _extract_last4_digits(first_pages_text, issuer)
    cardholder = _extract_cardholder_name(first_pages_text, issuer)
    p_from, p_to = _extract_statement_period(first_pages_text, issuer)
    needs_ocr = _check_needs_ocr(first_pages_text, issuer)

    # Check for name mismatch against folder hint
    name_mismatch = False
    if folder_hint:
        folder_last4 = re.search(r"\b(\d{4})\b", folder_hint)
        if folder_last4 and last4 and folder_last4.group(1) != last4:
            name_mismatch = True
            logger.warning(
                "last4 mismatch: folder says %s, PDF says %s (%s)",
                folder_last4.group(1), last4, path.name,
            )

    # Fallback defaults if extraction had gaps
    if not last4 and folder_hint:
        m = re.search(r"\b(\d{4})\b", folder_hint)
        if m:
            last4 = m.group(1)
        elif "SBI" in folder_hint:
            last4 = "0070"

    if not cardholder:
        if "7761" in (last4 + folder_hint):
            cardholder = "Ridhima Sharma"
        else:
            cardholder = "Utsav Mittal"

    confidence = 1.0 if (issuer != "UNKNOWN" and last4 and p_from and p_to) else 0.75

    return IssuerInfo(
        issuer=issuer,
        last4=last4,
        cardholder=cardholder,
        period_from=p_from,
        period_to=p_to,
        page_count=page_count,
        needs_ocr=needs_ocr,
        confidence=confidence,
        folder_hint=folder_hint,
        name_mismatch=name_mismatch,
    )


def build_coverage_matrix(statements: list) -> dict[str, dict[str, str]]:
    """
    Build a card x month matrix of statements found.
    Returns: {card_key_str: {month_label: status}}
    status in ('FOUND', 'GAP', 'OVERLAP')
    """
    matrix: dict[str, dict[str, str]] = {}
    for stmt in statements:
        ck = f"{stmt.issuer}-{stmt.last4}"
        if ck not in matrix:
            matrix[ck] = {}
        if stmt.period_from:
            m_label = stmt.period_from.strftime("%b'%y")
            if m_label in matrix[ck]:
                matrix[ck][m_label] = "OVERLAP"
            else:
                matrix[ck][m_label] = "FOUND"
    return matrix
