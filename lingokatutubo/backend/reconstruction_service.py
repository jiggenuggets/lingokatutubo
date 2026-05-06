"""
PDF reconstruction service - rebuilds PDF with translated text while preserving layout
"""

import fitz  # PyMuPDF
import os
from typing import List, Dict, Any, Optional, Tuple


class ReconstructionService:
    """Rebuilds PDFs with translated text while preserving original layout"""

    MIN_FONT_SIZE = 5.5
    FALLBACK_REVIEW_TEXT = "[UNKNOWN_FOR_REVIEW]"
    _unicode_fontfile_checked = False
    _unicode_fontfile_path: Optional[str] = None

    @staticmethod
    def _append_warning(warnings: Optional[List[str]], message: str) -> None:
        if warnings is not None and message and message not in warnings:
            warnings.append(message)

    @staticmethod
    def _coerce_translation_value(value: Any, fallback: str) -> str:
        """Support both legacy string translations and dict translation records."""
        if value is None:
            return fallback
        if isinstance(value, dict):
            translated = value.get("translated")
            if translated is None:
                return fallback
            return str(translated)
        return str(value)

    @classmethod
    def _translated_text_for_line(
        cls,
        translations: Dict[str, Any],
        lookup_key: str,
        original_text: str,
    ) -> str:
        """Resolve translated text by layout key first, then by original source text."""
        candidate_keys = [lookup_key, original_text, original_text.strip()]
        for key in candidate_keys:
            if key and key in translations:
                return cls._coerce_translation_value(translations.get(key), original_text)
        return original_text

    @staticmethod
    def _rect_from_bbox(
        bbox: Any,
        page_rect: fitz.Rect,
        padding: float = 0.0,
    ) -> Optional[fitz.Rect]:
        if not bbox:
            return None
        try:
            x0, y0, x1, y1 = [float(value) for value in list(bbox)[:4]]
        except Exception:
            return None

        left = min(x0, x1) - padding
        right = max(x0, x1) + padding
        top = min(y0, y1) - padding
        bottom = max(y0, y1) + padding

        left = max(page_rect.x0, min(page_rect.x1, left))
        right = max(page_rect.x0, min(page_rect.x1, right))
        top = max(page_rect.y0, min(page_rect.y1, top))
        bottom = max(page_rect.y0, min(page_rect.y1, bottom))

        if right <= left or bottom <= top:
            return None
        return fitz.Rect(left, top, right, bottom)

    @staticmethod
    def _rgb_tuple(value: Any, default: Tuple[float, float, float]) -> Tuple[float, float, float]:
        if value is None:
            return default
        try:
            if isinstance(value, int):
                return (
                    ((value >> 16) & 255) / 255.0,
                    ((value >> 8) & 255) / 255.0,
                    (value & 255) / 255.0,
                )
            values = [float(item) for item in list(value)[:3]]
            if len(values) != 3:
                return default
            if any(item > 1.0 for item in values):
                values = [max(0.0, min(255.0, item)) / 255.0 for item in values]
            return tuple(max(0.0, min(1.0, item)) for item in values)  # type: ignore[return-value]
        except Exception:
            return default

    @classmethod
    def _line_color(cls, line: Dict[str, Any]) -> Tuple[float, float, float]:
        color = line.get("color")
        if color is None:
            spans = line.get("spans") or []
            if spans:
                color = spans[0].get("color")
        return cls._rgb_tuple(color, (0.0, 0.0, 0.0))

    @classmethod
    def _visible_text_color(
        cls,
        line: Dict[str, Any],
        warnings: Optional[List[str]],
        page_number: int,
        block_number: int,
        line_number: int,
    ) -> Tuple[float, float, float]:
        color = cls._line_color(line)
        luminance = (0.2126 * color[0]) + (0.7152 * color[1]) + (0.0722 * color[2])
        if luminance > 0.88:
            cls._append_warning(
                warnings,
                f"Page {page_number}, block {block_number}, line {line_number}: "
                "translated text color was too light for the white mask; using black.",
            )
            return (0.0, 0.0, 0.0)
        return color

    @staticmethod
    def _fontname_for_line(line: Dict[str, Any]) -> str:
        spans = line.get("spans") or []
        span = spans[0] if spans else {}
        font_label = str(line.get("font") or span.get("font") or "").lower()
        flags = line.get("flags")
        if flags is None:
            flags = span.get("flags", 0)
        try:
            flags = int(flags or 0)
        except Exception:
            flags = 0

        is_bold = "bold" in font_label or "black" in font_label or bool(flags & 16)
        is_italic = "italic" in font_label or "oblique" in font_label or bool(flags & 2)

        if "mono" in font_label or "courier" in font_label or bool(flags & 8):
            if is_bold and is_italic:
                return "cobi"
            if is_bold:
                return "cobo"
            if is_italic:
                return "coit"
            return "cour"

        if "times" in font_label or "serif" in font_label or bool(flags & 4):
            if is_bold and is_italic:
                return "tibi"
            if is_bold:
                return "tibo"
            if is_italic:
                return "tiit"
            return "tiro"

        if is_bold and is_italic:
            return "hebi"
        if is_bold:
            return "hebo"
        if is_italic:
            return "heit"
        return "helv"

    @classmethod
    def _unicode_fontfile(cls) -> Optional[str]:
        if cls._unicode_fontfile_checked:
            return cls._unicode_fontfile_path

        candidates = []
        env_font = os.environ.get("LINGOKATUTUBO_PDF_FONT")
        if env_font:
            candidates.append(env_font)
        candidates.extend([
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\calibri.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        ])

        cls._unicode_fontfile_path = next(
            (path for path in candidates if path and os.path.exists(path)),
            None,
        )
        cls._unicode_fontfile_checked = True
        return cls._unicode_fontfile_path

    @staticmethod
    def _needs_unicode_font(text: str) -> bool:
        return any(ord(char) > 255 for char in text)

    @staticmethod
    def _pdf_safe_text(text: str) -> str:
        return "".join(char if ord(char) <= 255 else "?" for char in text)

    @staticmethod
    def _font(fontname: str, fontfile: Optional[str] = None) -> fitz.Font:
        if fontfile:
            try:
                return fitz.Font(fontfile=fontfile)
            except Exception:
                pass
        try:
            return fitz.Font(fontname=fontname)
        except Exception:
            return fitz.Font(fontname="helv")

    @staticmethod
    def _text_width(font: fitz.Font, text: str, fontsize: float) -> float:
        try:
            return float(font.text_length(text, fontsize=fontsize))
        except Exception:
            return len(text) * fontsize * 0.55

    @classmethod
    def _split_word(
        cls,
        word: str,
        font: fitz.Font,
        fontsize: float,
        max_width: float,
    ) -> List[str]:
        chunks: List[str] = []
        current = ""
        for char in word:
            candidate = current + char
            if current and cls._text_width(font, candidate, fontsize) > max_width:
                chunks.append(current)
                current = char
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks or [word]

    @classmethod
    def _wrap_text(
        cls,
        text: str,
        font: fitz.Font,
        fontsize: float,
        max_width: float,
    ) -> List[str]:
        wrapped: List[str] = []
        paragraphs = text.splitlines() or [text]
        for paragraph in paragraphs:
            words = paragraph.split()
            if not words:
                wrapped.append("")
                continue

            current = ""
            for word in words:
                word_chunks = (
                    [word]
                    if cls._text_width(font, word, fontsize) <= max_width
                    else cls._split_word(word, font, fontsize, max_width)
                )
                for chunk in word_chunks:
                    candidate = chunk if not current else f"{current} {chunk}"
                    if (
                        current
                        and cls._text_width(font, candidate, fontsize) > max_width
                    ):
                        wrapped.append(current)
                        current = chunk
                    else:
                        current = candidate
            if current:
                wrapped.append(current)
        return wrapped

    @classmethod
    def _ellipsize(
        cls,
        line: str,
        font: fitz.Font,
        fontsize: float,
        max_width: float,
    ) -> str:
        suffix = "..."
        if cls._text_width(font, suffix, fontsize) > max_width:
            return ""
        candidate = line.rstrip()
        while candidate and cls._text_width(font, candidate + suffix, fontsize) > max_width:
            candidate = candidate[:-1].rstrip()
        return (candidate + suffix) if candidate else suffix

    @staticmethod
    def _line_height(fontsize: float) -> float:
        return fontsize * 1.18

    @classmethod
    def _base_font_size(cls, line: Dict[str, Any], rect: fitz.Rect) -> float:
        size = line.get("size")
        if size is None:
            spans = line.get("spans") or []
            if spans:
                size = spans[0].get("size")
        try:
            size = float(size)
        except Exception:
            size = min(11.0, max(cls.MIN_FONT_SIZE, rect.height * 0.72))

        max_single_line_size = max(cls.MIN_FONT_SIZE, rect.height * 0.82)
        return max(cls.MIN_FONT_SIZE, min(size, max_single_line_size))

    @classmethod
    def _fit_text_to_rect(
        cls,
        text: str,
        rect: fitz.Rect,
        fontname: str,
        base_font_size: float,
        fontfile: Optional[str] = None,
    ) -> Tuple[str, float, bool, bool]:
        font = cls._font(fontname, fontfile=fontfile)
        max_width = max(1.0, rect.width)
        max_height = max(1.0, rect.height)
        fontsize = base_font_size
        shrunk = False

        while fontsize >= cls.MIN_FONT_SIZE:
            lines = cls._wrap_text(text, font, fontsize, max_width)
            required_height = max(1, len(lines)) * cls._line_height(fontsize)
            if required_height <= max_height + 0.25:
                return "\n".join(lines), fontsize, shrunk, False
            fontsize -= 0.5
            shrunk = True

        fontsize = cls.MIN_FONT_SIZE
        lines = cls._wrap_text(text, font, fontsize, max_width)
        max_lines = max(1, int(max_height / cls._line_height(fontsize)))
        truncated = len(lines) > max_lines
        if truncated:
            lines = lines[:max_lines]
            if lines:
                lines[-1] = cls._ellipsize(lines[-1], font, fontsize, max_width)
        return "\n".join(lines), fontsize, True, truncated

    @classmethod
    def _insert_text_in_rect(
        cls,
        page: fitz.Page,
        rect: fitz.Rect,
        text: str,
        line: Dict[str, Any],
        warnings: Optional[List[str]],
        page_number: int,
        block_number: int,
        line_number: int,
        fallback_text: Optional[str] = None,
    ) -> bool:
        fallback_text = (fallback_text or cls.FALLBACK_REVIEW_TEXT).strip() or cls.FALLBACK_REVIEW_TEXT
        fontname = cls._fontname_for_line(line)
        unicode_fontfile = cls._unicode_fontfile() if cls._needs_unicode_font(text) else None
        if cls._needs_unicode_font(text) and not unicode_fontfile:
            cls._append_warning(
                warnings,
                f"Page {page_number}, block {block_number}, line {line_number}: "
                "no Unicode PDF font was found; unsupported translated characters were replaced.",
            )
            text = cls._pdf_safe_text(text)

        base_font_size = cls._base_font_size(line, rect)
        fitted_text, fontsize, shrunk, truncated = cls._fit_text_to_rect(
            text,
            rect,
            "LinguaSans" if unicode_fontfile else fontname,
            base_font_size,
            fontfile=unicode_fontfile,
        )
        if not fitted_text.strip():
            cls._append_warning(
                warnings,
                f"Page {page_number}, block {block_number}, line {line_number}: "
                "translated text could not fit inside its bbox; using visible fallback text.",
            )
            fitted_text, fontsize, shrunk, truncated = cls._fit_text_to_rect(
                fallback_text,
                rect,
                "helv",
                min(base_font_size, 8.0),
            )

        if shrunk:
            cls._append_warning(
                warnings,
                f"Page {page_number}, block {block_number}, line {line_number}: "
                f"translated text was too long for its bbox; font reduced to {fontsize:.1f}pt.",
            )
        if truncated:
            cls._append_warning(
                warnings,
                f"Page {page_number}, block {block_number}, line {line_number}: "
                "translated text exceeded its bbox and was truncated.",
            )

        color = cls._visible_text_color(
            line,
            warnings,
            page_number,
            block_number,
            line_number,
        )
        candidates: List[Tuple[str, Optional[str]]] = (
            [("LinguaSans", unicode_fontfile)]
            if unicode_fontfile
            else [(fontname, None), ("helv", None)]
        )

        for candidate_font, candidate_fontfile in candidates:
            try:
                insert_args = {
                    "fontsize": fontsize,
                    "fontname": candidate_font,
                    "color": color,
                    "align": fitz.TEXT_ALIGN_LEFT,
                    "overlay": True,
                }
                if candidate_fontfile:
                    insert_args["fontfile"] = candidate_fontfile
                result = page.insert_textbox(
                    rect,
                    fitted_text,
                    **insert_args,
                )
                if result >= -0.01:
                    return True
            except Exception as e:
                if candidate_fontfile:
                    cls._append_warning(
                        warnings,
                        f"Page {page_number}, block {block_number}, line {line_number}: "
                        f"failed to insert translated text with Unicode font: {e}",
                    )
                elif candidate_font == "helv":
                    cls._append_warning(
                        warnings,
                        f"Page {page_number}, block {block_number}, line {line_number}: "
                        f"failed to insert translated text: {e}",
                    )
        cls._append_warning(
            warnings,
            f"Page {page_number}, block {block_number}, line {line_number}: "
            "translated text did not fit after font reduction; using visible fallback text.",
        )
        fallback_candidates = []
        for candidate in (fallback_text, cls.FALLBACK_REVIEW_TEXT):
            safe_candidate = cls._pdf_safe_text(candidate)
            if safe_candidate and safe_candidate not in fallback_candidates:
                fallback_candidates.append(safe_candidate)

        for candidate in fallback_candidates:
            fitted_fallback, fallback_size, _, _ = cls._fit_text_to_rect(
                candidate,
                rect,
                "helv",
                min(base_font_size, 8.0),
            )
            visible_fallback = fitted_fallback.strip() or cls.FALLBACK_REVIEW_TEXT
            try:
                result = page.insert_textbox(
                    rect,
                    visible_fallback,
                    fontsize=fallback_size,
                    fontname="helv",
                    color=(0.0, 0.0, 0.0),
                    align=fitz.TEXT_ALIGN_LEFT,
                    overlay=True,
                )
                if result >= -0.01:
                    return True
            except Exception:
                pass

            try:
                baseline_y = max(rect.y0 + cls.MIN_FONT_SIZE, min(rect.y1 - 1, rect.y0 + fallback_size))
                page.insert_text(
                    (rect.x0 + 0.5, baseline_y),
                    visible_fallback.splitlines()[0],
                    fontsize=max(4.0, min(fallback_size, cls.MIN_FONT_SIZE)),
                    fontname="helv",
                    color=(0.0, 0.0, 0.0),
                    overlay=True,
                )
                return True
            except Exception as e:
                cls._append_warning(
                    warnings,
                    f"Page {page_number}, block {block_number}, line {line_number}: "
                    f"failed to insert fallback text: {e}",
                )

        return False
    
    @staticmethod
    def reconstruct_pdf(
        input_pdf_path: str,
        layout_data: List[Dict[str, Any]],
        translations: Dict[str, Any],
        output_path: str,
        is_scanned: bool = False,
        layout_warnings: Optional[List[str]] = None,
    ) -> bool:
        """
        Reconstruct a PDF with translated text
        
        Args:
            input_pdf_path: Path to original PDF
            layout_data: Layout information from extraction
            translations: Dict mapping block_id -> {"original", "translated"}
            output_path: Path to save new PDF
            is_scanned: Whether layout came from OCR over scanned pages
            layout_warnings: Optional list populated with fit/overflow warnings
        
        Returns:
            True if successful
        """
        try:
            doc = fitz.open(input_pdf_path)
            
            # Process each page
            for page_num, page_layout in enumerate(layout_data):
                if page_num >= doc.page_count:
                    break
                
                page = doc[page_num]
                blocks = page_layout.get("blocks", [])

                layout_width = page_layout.get("width")
                layout_height = page_layout.get("height")
                if layout_width and abs(float(layout_width) - page.rect.width) > 1:
                    ReconstructionService._append_warning(
                        layout_warnings,
                        f"Page {page_num + 1}: layout width differs from source PDF page width.",
                    )
                if layout_height and abs(float(layout_height) - page.rect.height) > 1:
                    ReconstructionService._append_warning(
                        layout_warnings,
                        f"Page {page_num + 1}: layout height differs from source PDF page height.",
                    )

                # Add translated text over the original page. The original PDF
                # remains the base layer, so images/vector objects are retained.
                for block_idx, block in enumerate(blocks, start=1):
                    if block.get("type") != "text":
                        continue
                    
                    lines = block.get("lines", [])
                    
                    for line_idx, line in enumerate(lines, start=1):
                        original_text = line.get("text", "")
                        lookup_key = f"{page_num}_{block_idx - 1}_{line_idx - 1}"
                        translated_text = ReconstructionService._translated_text_for_line(
                            translations,
                            lookup_key,
                            original_text,
                        )
                        line_bbox = line.get("bbox")

                        if not line_bbox or not translated_text:
                            continue

                        if translated_text.strip() == original_text.strip():
                            continue

                        rect = ReconstructionService._rect_from_bbox(
                            line_bbox,
                            page.rect,
                            padding=0.75 if is_scanned else 0.5,
                        )
                        if rect is None:
                            ReconstructionService._append_warning(
                                layout_warnings,
                                f"Page {page_num + 1}, block {block_idx}, line {line_idx}: "
                                "invalid text bbox; translated text was skipped.",
                            )
                            continue

                        try:
                            page.draw_rect(
                                rect,
                                color=(1, 1, 1),
                                fill=(1, 1, 1),
                                width=0,
                                overlay=True,
                            )
                            inserted = ReconstructionService._insert_text_in_rect(
                                page,
                                rect,
                                translated_text,
                                line,
                                layout_warnings,
                                page_num + 1,
                                block_idx,
                                line_idx,
                                fallback_text=original_text or ReconstructionService.FALLBACK_REVIEW_TEXT,
                            )
                            if not inserted:
                                ReconstructionService._append_warning(
                                    layout_warnings,
                                    f"Page {page_num + 1}, block {block_idx}, line {line_idx}: "
                                    "white mask was drawn but no replacement text could be inserted.",
                                )
                        except Exception as e:
                            print(f"[Reconstruction] Error inserting text: {e}")
                            ReconstructionService._append_warning(
                                layout_warnings,
                                f"Page {page_num + 1}, block {block_idx}, line {line_idx}: "
                                f"failed to draw translated text: {e}",
                            )
            
            # Save output PDF
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            doc.save(output_path)
            doc.close()
            
            return True
        
        except Exception as e:
            print(f"[Reconstruction] Error reconstructing PDF: {e}")
            return False
    
    @staticmethod
    def create_bilingual_pdf(
        original_pdf_path: str,
        translated_pdf_path: str,
        output_path: str
    ) -> bool:
        """
        Create a bilingual PDF with original and translated pages side-by-side or alternating
        
        Args:
            original_pdf_path: Path to original PDF
            translated_pdf_path: Path to translated PDF
            output_path: Path to save combined PDF
        
        Returns:
            True if successful
        """
        try:
            original_doc = fitz.open(original_pdf_path)
            translated_doc = fitz.open(translated_pdf_path)
            output_doc = fitz.open()
            
            page_count = min(original_doc.page_count, translated_doc.page_count)
            
            # Alternate pages: original, translated, original, translated...
            for i in range(page_count):
                # Add original page
                orig_page = original_doc[i]
                output_doc.insert_pdf(original_doc, from_page=i, to_page=i)
                
                # Add translated page
                if i < translated_doc.page_count:
                    output_doc.insert_pdf(translated_doc, from_page=i, to_page=i)
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            output_doc.save(output_path)
            output_doc.close()
            original_doc.close()
            translated_doc.close()
            
            return True
        
        except Exception as e:
            print(f"[Reconstruction] Error creating bilingual PDF: {e}")
            return False
    
    @staticmethod
    def create_preview_images(
        pdf_path: str,
        output_dir: str,
        max_pages: int = 3,
        dpi: int = 150,
        prefix: str = "preview",
    ) -> List[str]:
        """
        Create preview images from PDF pages
        
        Args:
            pdf_path: Path to PDF
            output_dir: Directory to save preview images
            max_pages: Maximum pages to preview
            dpi: DPI for rendering
            prefix: Filename prefix, used to avoid original/translated collisions
        
        Returns:
            List of image file paths
        """
        image_paths = []
        
        try:
            doc = fitz.open(pdf_path)
            os.makedirs(output_dir, exist_ok=True)
            
            for page_num in range(min(max_pages, doc.page_count)):
                page = doc[page_num]
                
                # Render to image
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat)
                
                safe_prefix = "".join(
                    char if char.isalnum() or char in ("-", "_") else "_"
                    for char in prefix
                ) or "preview"
                image_path = os.path.join(output_dir, f"{safe_prefix}_page_{page_num}.png")
                pix.save(image_path)
                image_paths.append(image_path)
            
            doc.close()
        
        except Exception as e:
            print(f"[Reconstruction] Error creating preview images: {e}")
        
        return image_paths


# Global instance
_reconstruction_service = ReconstructionService()


def get_reconstruction_service() -> ReconstructionService:
    """Get the reconstruction service"""
    return _reconstruction_service
