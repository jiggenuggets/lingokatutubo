"""
Text extraction service for digital PDFs and DOCX files
"""

import fitz  # PyMuPDF
from docx import Document
from typing import List, Dict, Any, Optional
from .models import TextSegment


class ExtractionService:
    """Extracts text and layout information from digital documents"""

    @staticmethod
    def _bbox_to_list(bbox: Any) -> Optional[List[float]]:
        """Normalize PyMuPDF bbox/Rect values to JSON-safe [x0, y0, x1, y1]."""
        if bbox is None:
            return None
        try:
            if isinstance(bbox, fitz.Rect):
                values = [bbox.x0, bbox.y0, bbox.x1, bbox.y1]
            else:
                values = list(bbox)
            if len(values) != 4:
                return None
            return [float(value) for value in values]
        except Exception:
            return None

    @staticmethod
    def _point_to_list(point: Any) -> Optional[List[float]]:
        if point is None:
            return None
        try:
            values = list(point)
            if len(values) < 2:
                return None
            return [float(values[0]), float(values[1])]
        except Exception:
            return None

    @staticmethod
    def _color_to_rgb(color: Any) -> Optional[List[float]]:
        """Return an RGB triple in PyMuPDF's 0..1 drawing range."""
        if color is None:
            return None
        try:
            if isinstance(color, int):
                return [
                    ((color >> 16) & 255) / 255.0,
                    ((color >> 8) & 255) / 255.0,
                    (color & 255) / 255.0,
                ]
            values = list(color)
            if len(values) < 3:
                return None
            rgb = [float(values[0]), float(values[1]), float(values[2])]
            if any(value > 1.0 for value in rgb):
                rgb = [max(0.0, min(255.0, value)) / 255.0 for value in rgb]
            return [max(0.0, min(1.0, value)) for value in rgb]
        except Exception:
            return None

    @classmethod
    def _span_metadata(cls, span: Dict[str, Any]) -> Dict[str, Any]:
        metadata = {
            "text": span.get("text", ""),
            "bbox": cls._bbox_to_list(span.get("bbox")),
            "font": span.get("font", ""),
            "size": span.get("size"),
            "flags": span.get("flags"),
            "color": cls._color_to_rgb(span.get("color")),
            "origin": cls._point_to_list(span.get("origin")),
        }
        return {key: value for key, value in metadata.items() if value is not None}

    @classmethod
    def _image_block_metadata(cls, block: Dict[str, Any]) -> Dict[str, Any]:
        """Keep useful image geometry/metadata without embedding image bytes in JSON."""
        metadata_keys = (
            "number",
            "width",
            "height",
            "ext",
            "colorspace",
            "xres",
            "yres",
            "bpc",
            "size",
            "transform",
        )
        image_block: Dict[str, Any] = {
            "type": "image",
            "bbox": cls._bbox_to_list(block.get("bbox")),
        }
        for key in metadata_keys:
            if key not in block:
                continue
            value = block.get(key)
            if key == "transform":
                try:
                    value = [float(item) for item in list(value)]
                except Exception:
                    continue
            image_block[key] = value
        return image_block

    @classmethod
    def _extract_table_blocks(cls, page: fitz.Page) -> List[Dict[str, Any]]:
        """Best-effort digital-PDF table candidates from PyMuPDF, when available."""
        if not hasattr(page, "find_tables"):
            return []

        table_blocks: List[Dict[str, Any]] = []
        try:
            table_finder = page.find_tables()
            tables = getattr(table_finder, "tables", []) or []
            for idx, table in enumerate(tables):
                bbox = cls._bbox_to_list(getattr(table, "bbox", None))
                if not bbox:
                    continue
                cells = getattr(table, "cells", None) or []
                table_blocks.append({
                    "type": "table",
                    "bbox": bbox,
                    "table_index": idx,
                    "row_count": getattr(table, "row_count", None),
                    "col_count": getattr(table, "col_count", None),
                    "cell_count": len(cells),
                })
        except Exception as e:
            print(f"[Extraction] Table detection skipped: {e}")
        return table_blocks

    @classmethod
    def _extract_drawing_blocks(cls, page: fitz.Page) -> List[Dict[str, Any]]:
        """Capture vector drawings such as lines, rectangles, and table borders."""
        drawing_blocks: List[Dict[str, Any]] = []
        try:
            drawings = page.get_drawings()
        except Exception as e:
            print(f"[Extraction] Drawing extraction skipped: {e}")
            return drawing_blocks

        for idx, drawing in enumerate(drawings):
            bbox = cls._bbox_to_list(drawing.get("rect"))
            if not bbox:
                continue
            drawing_blocks.append({
                "type": "drawing",
                "bbox": bbox,
                "drawing_index": idx,
                "drawing_type": drawing.get("type"),
                "stroke_color": cls._color_to_rgb(drawing.get("color")),
                "fill_color": cls._color_to_rgb(drawing.get("fill")),
                "line_width": drawing.get("width"),
                "items_count": len(drawing.get("items", []) or []),
            })
        return drawing_blocks
    
    @staticmethod
    def extract_pdf_text_and_layout(pdf_path: str) -> List[Dict[str, Any]]:
        """
        Extract text and layout information from a digital PDF
        
        Args:
            pdf_path: Path to PDF file
        
        Returns:
            List of pages, each with text blocks and their positions
        """
        pages_data = []
        
        try:
            doc = fitz.open(pdf_path)
            
            for page_num in range(doc.page_count):
                page = doc[page_num]
                page_height = page.rect.height
                page_width = page.rect.width
                
                # Extract text with bounding boxes
                text_dict = page.get_text("dict")
                blocks = text_dict.get("blocks", [])
                
                page_data = {
                    "page": page_num,
                    "width": float(page_width),
                    "height": float(page_height),
                    "rotation": page.rotation,
                    "blocks": []
                }
                
                for block in blocks:
                    if block.get("type") == 0:  # Text block
                        block_bbox = ExtractionService._bbox_to_list(block.get("bbox"))
                        block_lines = block.get("lines", [])
                        
                        block_data = {
                            "type": "text",
                            "bbox": block_bbox,
                            "lines": []
                        }
                        
                        for line in block_lines:
                            line_bbox = ExtractionService._bbox_to_list(line.get("bbox"))
                            line_text = ""
                            spans = line.get("spans", [])
                            span_metadata = []
                            
                            for span in spans:
                                line_text += span.get("text", "")
                                span_metadata.append(ExtractionService._span_metadata(span))
                            
                            if line_text.strip():
                                first_span = next(
                                    (
                                        span
                                        for span in spans
                                        if (span.get("text") or "").strip()
                                    ),
                                    spans[0] if spans else {},
                                )
                                block_data["lines"].append({
                                    "text": line_text,
                                    "bbox": line_bbox,
                                    "font": first_span.get("font", "") if first_span else "",
                                    "size": first_span.get("size") if first_span else None,
                                    "color": ExtractionService._color_to_rgb(first_span.get("color")) if first_span else None,
                                    "flags": first_span.get("flags") if first_span else None,
                                    "spans": span_metadata,
                                })
                        
                        if block_data["lines"]:
                            page_data["blocks"].append(block_data)
                    
                    elif block.get("type") == 1:  # Image block
                        page_data["blocks"].append(ExtractionService._image_block_metadata(block))

                page_data["blocks"].extend(ExtractionService._extract_table_blocks(page))
                page_data["blocks"].extend(ExtractionService._extract_drawing_blocks(page))
                
                pages_data.append(page_data)
            
            doc.close()
        
        except Exception as e:
            print(f"[Extraction] Error extracting PDF: {e}")
        
        return pages_data
    
    @staticmethod
    def extract_docx_text_and_layout(docx_path: str) -> List[Dict[str, Any]]:
        """
        Extract text from DOCX file
        
        DOCX doesn't have concept of "pages" but we'll return paragraphs with metadata
        
        Args:
            docx_path: Path to DOCX file
        
        Returns:
            List of pages with text blocks
        """
        pages_data = []
        
        try:
            doc = Document(docx_path)
            
            # Group into pages (rough estimate based on paragraph count)
            page_data = {
                "page": 0,
                "width": 612,  # Standard letter width in points
                "height": 792,  # Standard letter height in points
                "blocks": []
            }
            
            for para in doc.paragraphs:
                text = para.text.strip()
                if not text:
                    continue
                
                # Detect heading style
                is_heading = para.style.name.startswith("Heading")
                
                block_data = {
                    "type": "text",
                    "bbox": [50, len(page_data["blocks"]) * 20, 562, len(page_data["blocks"]) * 20 + 20],
                    "lines": [{
                        "text": text,
                        "bbox": [50, len(page_data["blocks"]) * 20, 562, len(page_data["blocks"]) * 20 + 20],
                        "font": para.style.font.name if para.style.font else "Calibri",
                        "is_heading": is_heading
                    }]
                }
                
                page_data["blocks"].append(block_data)
            
            pages_data.append(page_data)
        
        except Exception as e:
            print(f"[Extraction] Error extracting DOCX: {e}")
        
        return pages_data
    
    @staticmethod
    def extract_text_from_layout(pages_data: List[Dict[str, Any]]) -> List[TextSegment]:
        """
        Convert layout data to TextSegment list
        
        Args:
            pages_data: Output from extract_pdf_text_and_layout
        
        Returns:
            List of TextSegment objects
        """
        segments = []
        
        for page_data in pages_data:
            page_num = page_data.get("page", 0)
            blocks = page_data.get("blocks", [])
            
            for block in blocks:
                if block.get("type") != "text":
                    continue
                
                block_bbox = block.get("bbox")
                lines = block.get("lines", [])
                
                for line in lines:
                    text = line.get("text", "").strip()
                    if not text:
                        continue
                    
                    segment = TextSegment(
                        content=text,
                        page=page_num,
                        bbox=line.get("bbox", block_bbox),
                        font_info={"font": line.get("font", "")},
                        is_heading=line.get("is_heading", False),
                        is_list_item=text[0].isdigit() or text.startswith(("•", "-", "*"))
                    )
                    segments.append(segment)
        
        return segments


# Global instance
_extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    """Get the extraction service"""
    return _extraction_service

