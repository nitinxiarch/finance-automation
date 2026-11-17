"""
File type auto-detection for bank vs books uploads.
Detects by content, NOT by filename or upload slot.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.parsers.bank_parser import ParseError


class FileType(str, Enum):
    BANK_STATEMENT = "BANK_STATEMENT"
    BOOKS_EXPORT = "BOOKS_EXPORT"
    UNKNOWN = "UNKNOWN"


@dataclass
class DetectedFile:
    path: str
    file_type: FileType
    detected_period: Optional[str]
    row_count: Optional[int]
    file_size_bytes: int
    original_filename: str
    detection_note: str = ""


def detect_file(path: str, original_filename: str) -> DetectedFile:
    """
    Detect whether a file is a bank statement (.xlsx) or books export (.xls).
    Returns a DetectedFile with the detected type and metadata.
    """
    size = os.path.getsize(path)

    # 1. Check if it matches ICICI Bank Statement structure
    try:
        from app.parsers.bank_parser import is_bank_statement, parse_bank_statement
        if is_bank_statement(path):
            period = None
            rows = None
            note = "ICICI Detailed Statement"
            try:
                stmt = parse_bank_statement(path)
                period = f"{stmt.period_from} to {stmt.period_to}"
                rows = len(stmt.rows)
                note = f"ICICI Detailed Statement — A/C {stmt.account_number}"
            except Exception as e:
                note = f"ICICI Detailed Statement ({e})"
            return DetectedFile(
                path=path,
                file_type=FileType.BANK_STATEMENT,
                detected_period=period,
                row_count=rows,
                file_size_bytes=size,
                original_filename=original_filename,
                detection_note=note,
            )
    except Exception:
        pass

    # 2. Check if it matches Zoho Books export structure
    try:
        from app.parsers.books_parser import is_books_export, parse_books_export
        if is_books_export(path):
            period = None
            rows = None
            note = "Zoho Books Export"
            try:
                export = parse_books_export(path)
                period = f"{export.period_from} to {export.period_to}"
                rows = len(export.rows)
                note = f"Zoho Books — {export.organisation}"
            except Exception as e:
                note = f"Zoho Books ({e})"
            return DetectedFile(
                path=path,
                file_type=FileType.BOOKS_EXPORT,
                detected_period=period,
                row_count=rows,
                file_size_bytes=size,
                original_filename=original_filename,
                detection_note=note,
            )
    except Exception:
        pass

    return DetectedFile(
        path=path,
        file_type=FileType.UNKNOWN,
        detected_period=None,
        row_count=None,
        file_size_bytes=size,
        original_filename=original_filename,
        detection_note="File format not recognized. Upload an ICICI .xlsx statement or Zoho .xls export.",
    )


def detect_and_assign(
    path_a: str, filename_a: str,
    path_b: str, filename_b: str,
) -> tuple[DetectedFile, DetectedFile, bool]:
    """
    Detect types for two files and assign bank vs books.
    Returns (bank_file, books_file, was_swapped).
    Raises ParseError if either file is unrecognized or both are the same type.
    """
    det_a = detect_file(path_a, filename_a)
    det_b = detect_file(path_b, filename_b)

    types = {det_a.file_type, det_b.file_type}
    if FileType.UNKNOWN in types:
        unknown = det_a if det_a.file_type == FileType.UNKNOWN else det_b
        raise ParseError(
            f"Could not detect file type for '{unknown.original_filename}'. "
            "Upload an ICICI .xlsx statement and a Zoho Books .xls export."
        )
    if det_a.file_type == det_b.file_type:
        raise ParseError(
            f"Both files appear to be {det_a.file_type.value}. "
            "Upload one ICICI bank statement and one Zoho Books export."
        )

    if det_a.file_type == FileType.BANK_STATEMENT:
        return det_a, det_b, False
    else:
        return det_b, det_a, True  # swapped

