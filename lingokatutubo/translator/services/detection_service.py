"""
File detection service - determines if a PDF/image is digital or scanned

Phase 4 improvement: PDF type detection now uses a character-count threshold
across ALL pages instead of checking only the first 3 pages for *any* text.
A PDF that yields an average of fewer than MIN_CHARS_PER_PAGE meaningful
characters is treated as scanned even if a tiny text layer exists (e.g. a
single invisible watermark character per page does not make the document
"digital").
"""

import fitz  # PyMuPDF
from PIL import Image
from typing import Optional, Tuple
from .models import DetectionType, FileType


# Minimum average characters per page to classify a PDF as DIGITAL.
# Below this threshold the document is sent through the OCR path.
# 50 chars ≈ roughly one short sentence — enough to confirm real text.
MIN_CHARS_PER_PAGE: int = 50


class DetectionService:
    """Detects whether a document is digital (searchable text) or scanned (image-based)"""

    @staticmethod
    def _count_pdf_text_chars(pdf_path: str) -> Tuple[int, int]:
        """Return (total_char_count, page_count) for all pages of a PDF.

        Counts only non-whitespace characters so that an invisible or very
        sparse text layer (e.g. a single watermark glyph) does not trick the
        detector into choosing the digital path.
        """
        try:
            doc = fitz.open(pdf_path)
            total = 0
            count = doc.page_count
            for page_num in range(count):
                page = doc[page_num]
                text = page.get_text()
                # Strip whitespace — count only meaningful characters
                total += len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
            doc.close()
            return total, count
        except Exception as e:
            print(f"[Detection] Error counting PDF text chars: {e}")
            return 0, 1

    @classmethod
    def detect_pdf_type(cls, pdf_path: str) -> DetectionType:
        """Detect if PDF is digital (has text layer) or scanned (image only).

        Phase 4: uses average chars-per-page threshold instead of a binary
        "any text on first 3 pages" check.

        Args:
            pdf_path: Path to PDF file

        Returns:
            DetectionType.DIGITAL if avg chars/page >= MIN_CHARS_PER_PAGE,
            DetectionType.SCANNED otherwise.
        """
        try:
            total_chars, page_count = cls._count_pdf_text_chars(pdf_path)
            avg_chars = total_chars / max(page_count, 1)
            is_digital = avg_chars >= MIN_CHARS_PER_PAGE
            print(
                f"[Detection] PDF text chars: {total_chars} across {page_count} page(s) "
                f"(avg {avg_chars:.1f}/page) -> {'DIGITAL' if is_digital else 'SCANNED'}"
            )
            return DetectionType.DIGITAL if is_digital else DetectionType.SCANNED

        except Exception as e:
            print(f"[Detection] Error detecting PDF type: {e}")
            return DetectionType.SCANNED

    @staticmethod
    def detect_image_type(image_path: str) -> DetectionType:
        """Detect if image contains scanned document text.

        All image inputs (JPG, PNG) are treated as scanned documents because
        they have no embedded text layer by definition.

        Args:
            image_path: Path to image file

        Returns:
            DetectionType.SCANNED always (images are scanned by nature)
        """
        try:
            with Image.open(image_path) as img:
                # Validate the image is readable; keep existing validation behavior.
                if img.mode != "RGB":
                    converted = img.convert("RGB")
                    converted.close()

            return DetectionType.SCANNED

        except Exception as e:
            print(f"[Detection] Error detecting image type: {e}")
            return DetectionType.SCANNED

    @staticmethod
    def detect_docx_type(docx_path: str) -> DetectionType:
        """DOCX files are always digital (they contain text, not images).

        Args:
            docx_path: Path to DOCX file

        Returns:
            DetectionType.DIGITAL always
        """
        return DetectionType.DIGITAL


# Global instance
_detection_service = DetectionService()


def get_detection_service() -> DetectionService:
    """Get the detection service"""
    return _detection_service
