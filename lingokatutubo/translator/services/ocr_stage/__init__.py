"""OCR stage for scanned PDFs and images (Tesseract-backed)."""

from .ocr_service import OCRService, OCRUnavailableError, get_ocr_service

__all__ = ["OCRService", "OCRUnavailableError", "get_ocr_service"]
