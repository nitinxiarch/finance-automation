"""
app/cards/parsers/base.py
Shared utilities for all issuer parsers.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional


class BaseParser:
    """Shared utilities inherited by all issuer-specific parsers."""

    # ------------------------------------------------------------------
    # Amount parsing
    # ------------------------------------------------------------------
    _AMOUNT_CLEAN = re.compile(r"[₹`\s]")
    _AMOUNT_COMMA = re.compile(r",")

    def parse_indian_amount(self, s: str) -> int:
        """
        Parse an Indian-formatted amount string to paise (int).

        Handles:
            "1,060.82"       -> 106082
            "2,00,000.00"    -> 20000000
            "661,565.53"     -> 66156553
            "10,00,000"      -> 100000000  (no paise part)
        Strips ₹, backtick (ICICI), whitespace, commas.
        Returns integer paise.
        """
        cleaned = self._AMOUNT_CLEAN.sub("", s)
        cleaned = self._AMOUNT_COMMA.sub("", cleaned)
        cleaned = cleaned.strip()
        try:
            value = float(cleaned)
        except ValueError as exc:
            raise ValueError(f"Cannot parse amount: {s!r}") from exc
        return round(value * 100)

    # ------------------------------------------------------------------
    # Date parsing
    # ------------------------------------------------------------------
    _DATE_FMTS = [
        "%d/%m/%Y",   # 15/07/2026  (HDFC, Axis cycle)
        "%d/%m/%y",   # 09/08/26
        "%d %b %Y",   # 09 Aug 2026
        "%d %b '%y",  # 10 Apr '26  (Axis monthly)  — apostrophe-year
        "%d %b %y",   # 05 Jul 26   (IDFC)
        "%d-%m-%Y",
        "%d-%m-%y",
        "%Y-%m-%d",
    ]

    def parse_date(self, s: str, two_digit_year_base: int = 2000) -> date:
        """
        Parse a date string to a :class:`datetime.date`.

        Supported formats:
            DD/MM/YYYY, DD/MM/YY, DD Mon YYYY, DD Mon 'YY, DD Mon YY,
            DD-MM-YYYY, YYYY-MM-DD.

        Args:
            s: Raw date string, possibly with surrounding whitespace.
            two_digit_year_base: Centennial base for 2-digit years (default 2000).
        """
        raw = s.strip()
        # Normalise apostrophe-year:  "Apr '26" → "Apr 26"
        normalised = raw.replace("'", "")
        for fmt in self._DATE_FMTS:
            try:
                parsed = datetime.strptime(normalised, fmt).date()
                # Fix 2-digit year if strptime gave the wrong century
                if parsed.year < two_digit_year_base:
                    parsed = parsed.replace(year=parsed.year + 100)
                return parsed
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date: {s!r}")

    # ------------------------------------------------------------------
    # Garbled-text detection
    # ------------------------------------------------------------------
    _CID_RE = re.compile(r"\(cid:\d+\)")
    _DOUBLE_RE = re.compile(r"(.)\1{2,}")          # 3+ repeated chars

    def is_garbled(self, text: str) -> bool:
        """
        Return True if the text appears garbled (OCR artefacts).

        Checks:
        1. More than 5 % of characters are inside (cid:NNN) tokens.
        2. Doubled-letter sequences (SSTTAATTEEMMEENNTT) account for
           more than 20 % of text — sign of a doubled-encoding PDF.
        """
        if not text:
            return False
        total = len(text)

        # CID token check
        cid_chars = sum(len(m.group()) for m in self._CID_RE.finditer(text))
        if cid_chars / total > 0.05:
            return True

        # Doubled-letter check: strip whitespace, check every 2-char window
        stripped = re.sub(r"\s+", "", text)
        if len(stripped) < 4:
            return False
        doubled = sum(
            1 for i in range(0, len(stripped) - 1, 2)
            if stripped[i] == stripped[i + 1]
        )
        if doubled / (len(stripped) // 2) > 0.5:
            return True
        return False

    # ------------------------------------------------------------------
    # Reference extraction
    # ------------------------------------------------------------------
    _REF_PATTERNS = [
        re.compile(r"Ref#\s*([A-Z0-9/]+)", re.IGNORECASE),
        re.compile(r"\bRef No[.:]?\s*([A-Z0-9/]+)", re.IGNORECASE),
        re.compile(r"\bRef\s*:\s*([A-Z0-9/]+)", re.IGNORECASE),
        re.compile(r"\(([A-Z0-9/]{6,})\)"),           # (XXXXXX)
        re.compile(r"#\s*([A-Z0-9]{6,})"),
    ]

    def extract_reference(self, desc: str) -> str:
        """
        Extract a payment reference or Ref# from a description string.

        Returns the first match found, or empty string.
        """
        for pat in self._REF_PATTERNS:
            m = pat.search(desc)
            if m:
                return m.group(1).strip()
        return ""

    # ------------------------------------------------------------------
    # Description cleaning
    # ------------------------------------------------------------------
    _CLEAN_PATTERNS = [
        (re.compile(r"\(Ref#\s*[A-Z0-9/]+\)", re.IGNORECASE), ""),
        (re.compile(r"\bRef#\s*[A-Z0-9/]+", re.IGNORECASE), ""),
        (re.compile(r"\(cid:\d+\)"), ""),
        (re.compile(r"\s{2,}"), " "),
    ]

    def clean_description(self, desc: str) -> str:
        """Strip references, cid tokens, and extra whitespace from a description."""
        result = desc
        for pat, repl in self._CLEAN_PATTERNS:
            result = pat.sub(repl, result)
        return result.strip()

    # ------------------------------------------------------------------
    # PDF text extraction helper
    # ------------------------------------------------------------------
    @staticmethod
    def extract_pdf_pages(path: Path, max_pages: Optional[int] = None) -> list[str]:
        """
        Extract text from each page of a PDF using pdfplumber.

        Returns a list of page text strings (one per page).
        """
        import pdfplumber  # noqa: PLC0415
        pages: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages):
                if max_pages is not None and i >= max_pages:
                    break
                text = page.extract_text() or ""
                pages.append(text)
        return pages
