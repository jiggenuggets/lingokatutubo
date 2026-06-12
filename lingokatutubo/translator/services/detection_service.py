"""
File detection service - determines if a PDF/image is digital or scanned
"""

import fitz  # PyMuPDF
from PIL import Image
import numpy as np
from typing import Tuple
from .models import DetectionType, FileType


class DetectionService:
    """Detects whether a document is digital (searchable text) or scanned (image-based)"""
    
    @staticmethod
    def detect_pdf_type(pdf_path: str) -> DetectionType:
        """
        Detect if PDF is digital (has text layer) or scanned (image only)
        
        Args:
            pdf_path: Path to PDF file
        
        Returns:
            DetectionType.DIGITAL if text is extractable, SCANNED otherwise
        """
        try:
            doc = fitz.open(pdf_path)
            
            # Check first few pages for text
            text_found = False
            for page_num in range(min(3, doc.page_count)):
                page = doc[page_num]
                text = page.get_text().strip()
                if text:
                    text_found = True
                    break
            
            doc.close()
            
            return DetectionType.DIGITAL if text_found else DetectionType.SCANNED
        
        except Exception as e:
            print(f"[Detection] Error detecting PDF type: {e}")
            return DetectionType.SCANNED
    
    @staticmethod
    def detect_image_type(image_path: str) -> DetectionType:
        """
        Detect if image contains scanned document text
        
        Uses heuristics: high contrast, low color complexity
        
        Args:
            image_path: Path to image file
        
        Returns:
            DetectionType.SCANNED (images are assumed to be scanned documents)
        """
        try:
            with Image.open(image_path) as img:
                # Convert to RGB if necessary. The converted image is not
                # needed yet, but this keeps the existing validation behavior
                # while ensuring PIL file handles are closed promptly.
                if img.mode != 'RGB':
                    converted = img.convert('RGB')
                    converted.close()
            
            # For now, assume all images are scanned documents
            # In production, use more sophisticated heuristics:
            # - color histogram analysis
            # - edge detection for text
            # - DPI analysis
            
            return DetectionType.SCANNED
        
        except Exception as e:
            print(f"[Detection] Error detecting image type: {e}")
            return DetectionType.SCANNED
    
    @staticmethod
    def detect_docx_type(docx_path: str) -> DetectionType:
        """
        DOCX files are always digital (they contain text, not images)
        
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

