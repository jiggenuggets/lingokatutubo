"""
OCR service for scanned PDFs and images.

Uses pytesseract + Tesseract OCR. Returns layout_data in the SAME shape as
digital PDF extraction (page -> blocks -> lines, all bboxes in PDF points)
so downstream code does not need to special-case the OCR path. Each block
and line additionally carries a `confidence` field in the [0, 1] range.

If Tesseract is not installed/available, callers receive a clear
OCRUnavailableError instead of silent placeholder output.
"""

import io
import os
from typing import Any, Dict, List, Optional


class OCRUnavailableError(RuntimeError):
    """Raised when Tesseract / pytesseract are not usable on this system."""


class OCRService:
    """Tesseract-backed OCR for scanned input.

    Note: bboxes are reported in PDF points (1/72 inch), matching digital
    extraction. Internally we render at `dpi` and scale pixel boxes back.
    """

    def __init__(self, dpi: int = 200, lang: str = "eng"):
        self.dpi = dpi
        self.lang = lang
        self._verified: Optional[bool] = None

        # Honor an explicit binary path if the user provides one.
        tess_cmd = os.environ.get("TESSERACT_CMD")
        if tess_cmd:
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = tess_cmd
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def _ensure_available(self) -> None:
        if self._verified:
            return
        try:
            import pytesseract  # type: ignore
            pytesseract.get_tesseract_version()
        except Exception as e:
            self._verified = False
            raise OCRUnavailableError(
                f"Tesseract OCR is not available: {e}. "
                "Install Tesseract (https://github.com/tesseract-ocr/tesseract) "
                "and the pytesseract Python package, or set TESSERACT_CMD."
            )
        self._verified = True

    def is_available(self) -> bool:
        try:
            self._ensure_available()
            return True
        except OCRUnavailableError:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_pdf_text_and_layout(self, pdf_path: str) -> List[Dict[str, Any]]:
        """OCR every page of a scanned PDF.

        Returns layout_data in the same shape as
        ExtractionService.extract_pdf_text_and_layout, but with `confidence`
        on each block and line.
        """
        self._ensure_available()

        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
        from pytesseract import Output

        pages_data: List[Dict[str, Any]] = []
        scale = 72.0 / float(self.dpi)

        doc = fitz.open(pdf_path)
        try:
            for page_idx in range(doc.page_count):
                page = doc[page_idx]
                page_w_pt = float(page.rect.width)
                page_h_pt = float(page.rect.height)

                mat = fitz.Matrix(self.dpi / 72.0, self.dpi / 72.0)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = Image.open(io.BytesIO(pix.tobytes("png")))

                try:
                    data = pytesseract.image_to_data(
                        img, output_type=Output.DICT, lang=self.lang
                    )
                except Exception as e:
                    print(f"[OCR] page {page_idx + 1} failed: {e}")
                    pages_data.append({
                        "page": page_idx,
                        "width": page_w_pt,
                        "height": page_h_pt,
                        "blocks": [],
                        "ocr_error": str(e),
                    })
                    continue

                pages_data.append(self._build_page_layout(
                    page_idx, page_w_pt, page_h_pt, data, scale
                ))
        finally:
            doc.close()

        return pages_data

    def extract_image_text_and_layout(self, image_path: str) -> List[Dict[str, Any]]:
        """OCR a single image file (treated as a one-page document)."""
        self._ensure_available()

        import pytesseract
        from PIL import Image
        from pytesseract import Output

        img = Image.open(image_path)
        if img.mode != "RGB":
            img = img.convert("RGB")

        # The image has no intrinsic PDF page size; assume `self.dpi` so that
        # bboxes can be reported in points consistently.
        page_w_pt = img.width * 72.0 / float(self.dpi)
        page_h_pt = img.height * 72.0 / float(self.dpi)
        scale = 72.0 / float(self.dpi)

        try:
            data = pytesseract.image_to_data(
                img, output_type=Output.DICT, lang=self.lang
            )
        except Exception as e:
            return [{
                "page": 0,
                "width": page_w_pt,
                "height": page_h_pt,
                "blocks": [],
                "ocr_error": str(e),
            }]

        return [self._build_page_layout(0, page_w_pt, page_h_pt, data, scale)]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_page_layout(
        page_idx: int,
        page_w_pt: float,
        page_h_pt: float,
        data: Dict[str, Any],
        scale: float,
    ) -> Dict[str, Any]:
        """Group Tesseract word-level rows into block -> line structure."""
        n = len(data.get("text", []))

        # Step 1: aggregate words into lines, keyed by (block, paragraph, line).
        lines_acc: Dict[tuple, Dict[str, Any]] = {}
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if conf < 0:  # Tesseract uses -1 for non-word rows
                continue

            block_num = data["block_num"][i]
            par_num = data["par_num"][i]
            line_num = data["line_num"][i]
            x = data["left"][i]
            y = data["top"][i]
            w = data["width"][i]
            h = data["height"][i]

            key = (block_num, par_num, line_num)
            entry = lines_acc.get(key)
            if entry is None:
                entry = {
                    "block_num": block_num,
                    "words": [],
                    "x0": x, "y0": y,
                    "x1": x + w, "y1": y + h,
                    "confs": [],
                }
                lines_acc[key] = entry
            entry["words"].append(text)
            entry["x0"] = min(entry["x0"], x)
            entry["y0"] = min(entry["y0"], y)
            entry["x1"] = max(entry["x1"], x + w)
            entry["y1"] = max(entry["y1"], y + h)
            entry["confs"].append(conf)

        # Step 2: group lines into blocks, in original Tesseract order.
        blocks_acc: Dict[int, Dict[str, Any]] = {}
        order: List[int] = []
        for key in sorted(lines_acc.keys()):
            line = lines_acc[key]
            line_text = " ".join(line["words"]).strip()
            if not line_text:
                continue
            avg_conf_pct = sum(line["confs"]) / len(line["confs"])
            line_conf = max(0.0, min(1.0, avg_conf_pct / 100.0))
            line_bbox = [
                line["x0"] * scale,
                line["y0"] * scale,
                line["x1"] * scale,
                line["y1"] * scale,
            ]

            block_num = line["block_num"]
            block = blocks_acc.get(block_num)
            if block is None:
                block = {
                    "type": "text",
                    "lines": [],
                    "x0": line["x0"], "y0": line["y0"],
                    "x1": line["x1"], "y1": line["y1"],
                    "confs": [],
                }
                blocks_acc[block_num] = block
                order.append(block_num)

            block["lines"].append({
                "text": line_text,
                "bbox": line_bbox,
                "confidence": round(line_conf, 4),
                "font": "",
            })
            block["x0"] = min(block["x0"], line["x0"])
            block["y0"] = min(block["y0"], line["y0"])
            block["x1"] = max(block["x1"], line["x1"])
            block["y1"] = max(block["y1"], line["y1"])
            block["confs"].append(line_conf)

        # Step 3: emit blocks in document order with aggregate confidence.
        blocks_out: List[Dict[str, Any]] = []
        for bnum in order:
            b = blocks_acc[bnum]
            if not b["lines"]:
                continue
            block_conf = sum(b["confs"]) / len(b["confs"])
            blocks_out.append({
                "type": "text",
                "bbox": [
                    b["x0"] * scale, b["y0"] * scale,
                    b["x1"] * scale, b["y1"] * scale,
                ],
                "confidence": round(block_conf, 4),
                "lines": b["lines"],
            })

        return {
            "page": page_idx,
            "width": page_w_pt,
            "height": page_h_pt,
            "blocks": blocks_out,
        }


_ocr_service: Optional[OCRService] = None


def get_ocr_service() -> OCRService:
    """Return the global OCRService singleton."""
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OCRService()
    return _ocr_service
