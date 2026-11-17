"""
app/cards/parsers/__init__.py
Registry of issuer-specific statement parsers.
"""
from __future__ import annotations

import abc
from pathlib import Path

from app.cards.models import CardStatement


class StatementParser(abc.ABC):
    """Abstract base class for all credit-card statement parsers."""

    @abc.abstractmethod
    def parse(self, path: Path, file_hash: str) -> CardStatement:
        """Parse a PDF statement and return a fully-populated CardStatement."""


def get_parser(issuer: str) -> StatementParser:
    """
    Return the correct parser instance for the given issuer string.

    Accepted issuer values (case-insensitive):
        "hdfc", "axis", "icici", "idfc", "sbi"
    """
    key = issuer.strip().lower()
    if key == "hdfc":
        from app.cards.parsers.hdfc import HDFCParser
        return HDFCParser()
    if key == "axis":
        from app.cards.parsers.axis import AxisParser
        return AxisParser()
    if key == "icici":
        from app.cards.parsers.icici import ICICIParser
        return ICICIParser()
    if key == "idfc":
        from app.cards.parsers.idfc import IDFCParser
        return IDFCParser()
    if key == "sbi":
        from app.cards.parsers.sbi import SBIParser
        return SBIParser()
    raise ValueError(f"No parser registered for issuer: {issuer!r}")
