"""
OCR tier for card statement processing.

Renders PDF pages to images and runs Tesseract OCR on them.
Falls back gracefully when optional dependencies are unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OCRNotAvailableError(RuntimeError):
    """Raised when Tesseract is not installed or cannot be found."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class OCRResult:
    text: str
    page_index: int
    confidence: float  # 0.0 – 1.0; estimated from pytesseract mean conf


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_tesseract_confidence(data: dict) -> float:
    """Return mean Tesseract confidence (0-1) from image_to_data output."""
    confs = [int(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit() and int(c) >= 0]
    if not confs:
        return 0.5
    return round(sum(confs) / len(confs) / 100.0, 4)


def _render_page_pypdfium2(pdf_path: Path, page_index: int, dpi: int) -> "Image":
    """Render a single page using pypdfium2."""
    import pypdfium2 as pdfium  # type: ignore
    from PIL import Image  # type: ignore  # noqa: F401

    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[page_index]
    scale = dpi / 72.0
    bitmap = page.render(scale=scale, rotation=0)
    pil_image = bitmap.to_pil()
    page.close()
    doc.close()
    return pil_image


def _render_page_pdf2image(pdf_path: Path, page_index: int, dpi: int) -> "Image":
    """Render a single page using pdf2image (poppler) as fallback."""
    from pdf2image import convert_from_path  # type: ignore

    # pdf2image is 1-indexed; page_index is 0-indexed
    pages = convert_from_path(
        str(pdf_path),
        dpi=dpi,
        first_page=page_index + 1,
        last_page=page_index + 1,
    )
    if not pages:
        raise RuntimeError(f"pdf2image returned no pages for page_index={page_index}")
    return pages[0]


def _render_page(pdf_path: Path, page_index: int, dpi: int):
    """Render a single PDF page to a PIL Image, trying pypdfium2 first."""
    try:
        return _render_page_pypdfium2(pdf_path, page_index, dpi)
    except ImportError:
        logger.debug("pypdfium2 not available; falling back to pdf2image")
        return _render_page_pdf2image(pdf_path, page_index, dpi)


def _get_page_count(pdf_path: Path) -> int:
    """Return the number of pages in the PDF."""
    try:
        import pypdfium2 as pdfium  # type: ignore
        doc = pdfium.PdfDocument(str(pdf_path))
        count = len(doc)
        doc.close()
        return count
    except ImportError:
        pass

    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as pdf:
            return len(pdf.pages)
    except Exception:
        pass

    # Last resort – pdf2image
    from pdf2image import pdfinfo_from_path  # type: ignore
    info = pdfinfo_from_path(str(pdf_path))
    return info.get("Pages", 0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_and_ocr(
    pdf_path: Path,
    page_index: int,
    dpi: int = 300,
) -> OCRResult:
    """
    Render *page_index* (0-based) of *pdf_path* at *dpi* and OCR it.

    Returns an OCRResult.  Raises OCRNotAvailableError if Tesseract is not
    installed.
    """
    try:
        import pytesseract  # type: ignore
    except ImportError as exc:
        raise OCRNotAvailableError(
            "pytesseract is not installed. Install it with: pip install pytesseract\n"
            "Also ensure the Tesseract binary is on PATH: https://github.com/tesseract-ocr/tesseract"
        ) from exc

    # Verify tesseract binary is reachable
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRNotAvailableError(
            "Tesseract binary not found. Please install Tesseract and ensure it is on PATH.\n"
            "Windows: https://github.com/UB-Mannheim/tesseract/wiki\n"
            "Linux:   sudo apt-get install tesseract-ocr\n"
            "macOS:   brew install tesseract"
        ) from exc

    image = _render_page(pdf_path, page_index, dpi)

    # Run OCR to get text
    custom_config = "--psm 6"
    text: str = pytesseract.image_to_string(image, lang="eng", config=custom_config)

    # Estimate confidence from image_to_data
    try:
        import pytesseract as _pt  # already imported above
        data = _pt.image_to_data(image, lang="eng", config=custom_config, output_type=_pt.Output.DICT)
        confidence = _get_tesseract_confidence(data)
    except Exception:
        confidence = 0.5

    return OCRResult(text=text, page_index=page_index, confidence=confidence)


def ocr_pdf(
    pdf_path: Path,
    page_indices: Optional[list[int]] = None,
    dpi: int = 300,
) -> list[OCRResult]:
    """
    OCR some or all pages of *pdf_path*.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file.
    page_indices:
        0-based list of pages to process.  ``None`` means all pages.
    dpi:
        Render resolution (default 300 DPI).

    Returns
    -------
    list[OCRResult]
        One entry per requested page, in page order.
    """
    if page_indices is None:
        page_count = _get_page_count(pdf_path)
        page_indices = list(range(page_count))

    results: list[OCRResult] = []
    for idx in page_indices:
        try:
            result = render_and_ocr(pdf_path, idx, dpi=dpi)
            results.append(result)
            logger.debug("OCR page %d: confidence=%.2f, chars=%d", idx, result.confidence, len(result.text))
        except OCRNotAvailableError:
            raise
        except Exception as exc:
            logger.warning("OCR failed for page %d of %s: %s", idx, pdf_path.name, exc)
            results.append(OCRResult(text="", page_index=idx, confidence=0.0))

    return results
