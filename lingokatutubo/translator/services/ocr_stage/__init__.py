"""OCR stage for scanned PDFs and images (Tesseract-backed)."""

from .ocr_service import OCRService, OCRUnavailableError, get_ocr_service
from .qa_report import (
    calculate_cer,
    calculate_wer,
    audit_reading_order,
    build_document_qa_report,
    compare_psm_modes,
    DocumentQAReport,
    PageQAResult,
)
from .environment import (
    check_tesseract_environment,
    assert_tesseract_available,
    format_environment_report,
)

__all__ = [
    "OCRService",
    "OCRUnavailableError",
    "get_ocr_service",
    "calculate_cer",
    "calculate_wer",
    "audit_reading_order",
    "build_document_qa_report",
    "compare_psm_modes",
    "DocumentQAReport",
    "PageQAResult",
    "check_tesseract_environment",
    "assert_tesseract_available",
    "format_environment_report",
]
