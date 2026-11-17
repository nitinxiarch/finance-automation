"""
ZIP ingestion for card statements.

Safety: zip-slip prevention, nested archive blocking, size/count/page caps,
duplicate detection, and password-protected PDF support.
"""

from __future__ import annotations

import hashlib
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_PDFS = 200
MAX_TOTAL_BYTES = 500 * 1024 * 1024   # 500 MB
MAX_PAGES = 10_000

# File extensions treated as nested archives (blocked)
_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".bz2", ".xz", ".rar", ".7z", ".tgz"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ZipSafetyError(Exception):
    """Raised when a ZIP entry violates safety constraints."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IngestedFile:
    path: Path              # absolute path to the extracted file on disk
    zip_entry_name: str     # original name inside the ZIP
    folder_hint: str        # top-level folder from ZIP structure (or "")
    file_hash: str          # sha256 hex digest
    size_bytes: int


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------

def compute_file_hash(path: Path) -> str:
    """Return the SHA-256 hex digest of the file at *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# ZIP ingestion
# ---------------------------------------------------------------------------

def ingest_zip(
    zip_path: Path,
    extract_to: Path,
    passwords: Optional[dict[str, bytes]] = None,
) -> list[IngestedFile]:
    """
    Extract PDFs from *zip_path* into *extract_to*, applying safety checks.

    Args:
        zip_path:    Path to the ZIP file.
        extract_to:  Destination directory (created if absent).
        passwords:   Mapping from ZIP-entry filename (basename only) to
                     password bytes, for password-protected entries.

    Returns:
        List of :class:`IngestedFile` objects, sorted by (folder_hint, filename).

    Raises:
        :class:`ZipSafetyError` on zip-slip paths or nested archives.
        :class:`ValueError` if limits are exceeded.
    """
    if passwords is None:
        passwords = {}

    extract_to.mkdir(parents=True, exist_ok=True)

    seen_hashes: dict[str, str] = {}   # hash -> zip_entry_name (for dup detection)
    results: list[IngestedFile] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        entries = zf.infolist()

        # First pass: safety checks
        pdf_count = 0
        total_bytes = 0

        for entry in entries:
            name = entry.filename

            # --- Zip-slip prevention ---
            if ".." in name or name.startswith("/") or Path(name).is_absolute():
                raise ZipSafetyError(
                    f"Zip-slip or absolute path detected: {name!r}"
                )

            # --- Nested archive blocking ---
            suffix = Path(name).suffix.lower()
            if suffix in _ARCHIVE_EXTS:
                raise ZipSafetyError(
                    f"Nested archive not allowed: {name!r}"
                )

            if entry.is_dir():
                continue

            if suffix != ".pdf":
                logger.warning("Skipping non-PDF entry: %s", name)
                continue

            pdf_count += 1
            total_bytes += entry.file_size

        # --- Limit checks (pre-extraction) ---
        if pdf_count > MAX_PDFS:
            raise ValueError(
                f"ZIP contains {pdf_count} PDFs; limit is {MAX_PDFS}"
            )
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError(
                f"ZIP total PDF size {total_bytes} bytes exceeds {MAX_TOTAL_BYTES} bytes"
            )

        # Second pass: extract PDFs
        for entry in entries:
            name = entry.filename
            if entry.is_dir():
                continue
            suffix = Path(name).suffix.lower()
            if suffix != ".pdf":
                continue

            # Determine folder hint (first path component if any)
            parts = Path(name).parts
            folder_hint = parts[0] if len(parts) > 1 else ""

            # Flatten to safe output path
            safe_name = Path(name).name
            dest = extract_to / safe_name

            # Handle filename collisions from different folders
            if dest.exists():
                stem = Path(safe_name).stem
                ext = Path(safe_name).suffix
                i = 1
                while dest.exists():
                    dest = extract_to / f"{stem}_{i}{ext}"
                    i += 1

            # Extract with optional password
            pwd = passwords.get(safe_name) or passwords.get(name)
            try:
                data = zf.read(name, pwd=pwd)
            except RuntimeError as exc:
                # Password required but not provided
                raise ZipSafetyError(
                    f"Could not read {name!r} (password required?): {exc}"
                ) from exc

            dest.write_bytes(data)

            # Compute hash
            file_hash = compute_file_hash(dest)

            # Duplicate detection
            if file_hash in seen_hashes:
                logger.warning(
                    "Duplicate file skipped: %s (same content as %s)",
                    name,
                    seen_hashes[file_hash],
                )
                dest.unlink()
                continue

            seen_hashes[file_hash] = name

            results.append(
                IngestedFile(
                    path=dest,
                    zip_entry_name=name,
                    folder_hint=folder_hint,
                    file_hash=file_hash,
                    size_bytes=entry.file_size,
                )
            )

    # --- Page count check (post-extraction, requires pdfplumber) ---
    total_pages = 0
    try:
        import pdfplumber  # noqa: PLC0415

        for ingested in results:
            try:
                with pdfplumber.open(ingested.path) as pdf:
                    total_pages += len(pdf.pages)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not count pages in %s: %s", ingested.path, exc)

        if total_pages > MAX_PAGES:
            raise ValueError(
                f"ZIP total page count {total_pages} exceeds {MAX_PAGES}"
            )
    except ImportError:
        logger.warning("pdfplumber not available; skipping page count check")

    # Sort by folder_hint then filename
    results.sort(key=lambda f: (f.folder_hint.lower(), f.path.name.lower()))
    return results
