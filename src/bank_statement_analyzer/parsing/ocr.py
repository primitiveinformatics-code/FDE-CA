"""OCR fallback for scanned/image-only PDF statements (FR-1).

Only invoked when a page yields no usable text via pdfplumber. Raw
OCR'd text is always retained on `StatementMeta.ocr_raw_text` so the UI
can show it next to the extracted fields for manual verification before
the file proceeds, per the PRD.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MIN_USABLE_TEXT_CHARS = 20


class OcrUnavailableError(RuntimeError):
    """Raised when the Tesseract binary isn't installed/reachable."""


def page_needs_ocr(page_text: str | None) -> bool:
    return not page_text or len(page_text.strip()) < MIN_USABLE_TEXT_CHARS


def ocr_page(page) -> str:
    """OCR a single pdfplumber page. Requires the `tesseract` binary."""
    try:
        import pytesseract
    except ImportError as e:  # pragma: no cover - optional dependency
        raise OcrUnavailableError("pytesseract is not installed") from e

    try:
        image = page.to_image(resolution=300).original
        return pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError as e:
        raise OcrUnavailableError(
            "Tesseract OCR engine not found on this machine. Install it "
            "(e.g. `apt install tesseract-ocr` / the Windows installer) "
            "and retry."
        ) from e


def ocr_document(pdf) -> str:
    """OCR every page of an already-open pdfplumber PDF, concatenated."""
    chunks = []
    for page in pdf.pages:
        text = page.extract_text() or ""
        if page_needs_ocr(text):
            try:
                text = ocr_page(page)
            except OcrUnavailableError:
                logger.warning("OCR unavailable; page left blank")
                text = ""
        chunks.append(text)
    return "\n".join(chunks)
