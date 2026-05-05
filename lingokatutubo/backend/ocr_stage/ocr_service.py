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
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


class OCRUnavailableError(RuntimeError):
    """Raised when Tesseract / pytesseract are not usable on this system."""


class OCRService:
    """Tesseract-backed OCR for scanned input.

    Note: bboxes are reported in PDF points (1/72 inch), matching digital
    extraction. Internally we render at `dpi` and scale pixel boxes back.
    """

    def __init__(
        self,
        dpi: int = 200,
        lang: str = "eng",
        preprocess: bool = True,
        denoise: bool = True,
        detect_orientation: bool = True,
    ):
        self.dpi = dpi
        self.lang = lang
        self.preprocess_enabled = preprocess
        self.denoise_enabled = denoise
        self.detect_orientation_enabled = detect_orientation
        self._verified: Optional[bool] = None
        self._installed_languages: Optional[List[str]] = None
        # OSD support is checked lazily and cached. None = unknown, False = OSD
        # data missing or unusable, True = working.
        self._osd_available: Optional[bool] = None

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

    def get_installed_languages(self) -> List[str]:
        """Return Tesseract language packs reported by pytesseract."""
        self._ensure_available()
        return list(self._get_installed_tesseract_languages())

    def _get_installed_tesseract_languages(self) -> List[str]:
        """Cached wrapper around pytesseract.get_languages()."""
        if self._installed_languages is not None:
            return self._installed_languages

        try:
            import pytesseract  # type: ignore
            languages = pytesseract.get_languages(config="")
        except Exception as e:
            raise OCRUnavailableError(
                f"Tesseract OCR language list is not available: {e}. "
                "Install Tesseract language data files, or set TESSDATA_PREFIX."
            )

        self._installed_languages = [str(lang) for lang in languages]
        return self._installed_languages

    # ------------------------------------------------------------------
    # OCR language resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_language_list(
        languages: Optional[Union[str, Sequence[str]]]
    ) -> List[str]:
        if languages is None:
            return []
        if isinstance(languages, str):
            values = [languages]
        else:
            values = [str(value) for value in languages]

        parts: List[str] = []
        for value in values:
            normalized = value
            for sep in ("+", ",", ";", "|", "/"):
                normalized = normalized.replace(sep, ",")
            parts.extend(
                part.strip()
                for part in normalized.split(",")
                if part.strip()
            )
        return parts

    @staticmethod
    def _normalize_language_name(language: str) -> str:
        return language.strip().lower().replace("_", "-")

    @staticmethod
    def _format_language_list(languages: Sequence[str]) -> str:
        if not languages:
            return "none"
        return ", ".join(sorted(languages))

    @classmethod
    def _candidate_tesseract_codes(cls, language: str) -> List[str]:
        aliases = {
            "en": ["eng"],
            "eng": ["eng"],
            "english": ["eng"],
            "fil": ["fil"],
            "filipino": ["fil", "tgl"],
            "pilipino": ["fil", "tgl"],
            "tl": ["tgl"],
            "tgl": ["tgl"],
            "tagalog": ["tgl"],
            "ceb": ["ceb"],
            "cebuano": ["ceb"],
        }
        if language in aliases:
            return aliases[language]
        if len(language) == 3 and language.isalpha():
            return [language]
        return []

    @staticmethod
    def _is_bagobo_language(language: str) -> bool:
        return language in {
            "bagobo",
            "bagobo-tagabawa",
            "tagabawa",
            "bagobo tagabawa",
            "bagobo/tagabawa",
        }

    def resolve_tesseract_language(
        self,
        languages: Optional[Union[str, Sequence[str]]] = None,
    ) -> Tuple[str, List[str]]:
        """Resolve requested language names to a safe Tesseract lang string.

        Missing packs never bubble into a Tesseract crash. The resolver keeps
        installed requested packs, falls back to English when needed, and
        returns warnings for structure.json.
        """
        self._ensure_available()
        installed = set(self._get_installed_tesseract_languages())
        installed_ocr = {lang for lang in installed if lang != "osd"}
        requested = self._coerce_language_list(languages)
        if not requested:
            requested = self._coerce_language_list(self.lang) or ["eng"]

        selected: List[str] = []
        warnings: List[str] = []

        def add_warning(message: str) -> None:
            if message and message not in warnings:
                warnings.append(message)

        for raw_language in requested:
            language = self._normalize_language_name(raw_language)
            if language in {"auto", "detect", "unknown"}:
                continue

            if self._is_bagobo_language(language):
                add_warning(
                    "No Tesseract OCR language pack exists for Bagobo/Tagabawa; "
                    "using English OCR fallback for now."
                )
                continue

            candidates = self._candidate_tesseract_codes(language)
            if not candidates:
                add_warning(
                    f"Requested OCR language '{raw_language}' is not mapped to "
                    "a known Tesseract language pack; using English OCR fallback."
                )
                continue

            installed_candidate = next(
                (candidate for candidate in candidates if candidate in installed_ocr),
                None,
            )
            if installed_candidate:
                if installed_candidate not in selected:
                    selected.append(installed_candidate)
                continue

            add_warning(
                f"Requested OCR language '{raw_language}' requires Tesseract "
                f"pack(s) {' or '.join(candidates)}, but installed OCR packs "
                f"are {self._format_language_list(list(installed_ocr))}; "
                "using English OCR fallback."
            )

        if not selected:
            selected = ["eng"]

        if "eng" not in installed_ocr:
            add_warning(
                "Tesseract English language pack 'eng' is not installed; "
                "OCR may fail until eng.traineddata is installed."
            )

        return "+".join(selected), warnings

    # ------------------------------------------------------------------
    # Preprocessing + orientation
    # ------------------------------------------------------------------

    def _preprocess(self, img):
        """Grayscale + autocontrast + light denoise.

        Every operation here MUST preserve pixel dimensions so the existing
        DPI->points scaling stays valid. No resizing, no rotation.
        """
        if not self.preprocess_enabled:
            return img

        from PIL import ImageFilter, ImageOps

        try:
            if img.mode != "L":
                img = img.convert("L")
        except Exception as e:
            print(f"[OCR] grayscale failed: {e}")
            return img

        try:
            img = ImageOps.autocontrast(img, cutoff=2)
        except Exception as e:
            print(f"[OCR] autocontrast failed: {e}")

        if self.denoise_enabled:
            try:
                # Mild median filter knocks out salt-and-pepper noise from
                # low-quality scans; size=3 is small enough not to hurt
                # already-clean text noticeably.
                img = img.filter(ImageFilter.MedianFilter(size=3))
            except Exception as e:
                print(f"[OCR] median denoise failed: {e}")

        return img

    def _detect_rotation_deg(self, img) -> int:
        """Return clockwise rotation needed to make the image upright.

        Returns 0 / 90 / 180 / 270, or 0 if OSD is unavailable or fails.
        OSD requires Tesseract's `osd.traineddata` and a sufficient amount
        of text to be reliable; callers must treat 0 as "unknown" too.
        """
        if not self.detect_orientation_enabled:
            return 0
        if self._osd_available is False:
            return 0
        try:
            import pytesseract
            osd = pytesseract.image_to_osd(img, config="--psm 0")
            self._osd_available = True
            for line in osd.splitlines():
                if line.startswith("Rotate:"):
                    return int(line.split(":", 1)[1].strip()) % 360
            return 0
        except Exception as e:
            # Distinguish "osd.traineddata is not installed" (cache False so
            # we don't keep paying the cost) from "OSD ran but couldn't
            # decide on this image" (sparse page — try again on next page).
            msg = str(e).lower()
            permanent = (
                "osd.traineddata" in msg
                or "tessdata" in msg
                or "failed loading" in msg
                or "please make sure the tessdata" in msg
            )
            if permanent:
                self._osd_available = False
                print(f"[OCR] OSD permanently disabled: {e}")
            else:
                print(f"[OCR] OSD skipped on this page: {e}")
            return 0

    def _apply_rotation(self, img, rotate_deg: int):
        """Rotate the image to upright if we can do so safely.

        Returns (image, applied_deg, warning_or_none). We only physically
        rotate for 180 degrees — that case has trivial bbox inversion and
        preserves dimensions. For 90 / 270 we leave the image alone and
        surface a warning, because bbox transforms under 90-deg rotation
        require swapping page dimensions and we want to keep the JSON
        contract stable.
        """
        if rotate_deg == 0:
            return img, 0, None
        if rotate_deg == 180:
            from PIL import Image
            return img.transpose(Image.ROTATE_180), 180, None
        return img, 0, (
            f"Page appears rotated by {rotate_deg} degrees. "
            "OCR was run without rotation; results may be poor."
        )

    @staticmethod
    def _flip_page_layout_180(page_layout: Dict[str, Any]) -> Dict[str, Any]:
        """Map bboxes from a 180-rotated frame back to the original frame.

        Dimensions are unchanged under 180-degree rotation, so we only
        need to flip each bbox: (x0, y0, x1, y1) -> (W-x1, H-y1, W-x0, H-y0).
        """
        page_w = page_layout.get("width") or 0.0
        page_h = page_layout.get("height") or 0.0

        def flip(bbox):
            if not bbox or len(bbox) != 4:
                return bbox
            x0, y0, x1, y1 = bbox
            return [page_w - x1, page_h - y1, page_w - x0, page_h - y0]

        for block in page_layout.get("blocks", []):
            block["bbox"] = flip(block.get("bbox"))
            for line in block.get("lines", []):
                line["bbox"] = flip(line.get("bbox"))
        return page_layout

    def _ocr_image(
        self,
        img,
        page_idx: int,
        page_w_pt: float,
        page_h_pt: float,
        scale: float,
        lang: str,
        language_warnings: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Preprocess, orient, OCR, and build per-page layout for one image."""
        import pytesseract
        from pytesseract import Output

        warnings: List[str] = list(language_warnings or [])

        try:
            img = self._preprocess(img)
        except Exception as e:
            print(f"[OCR] preprocessing failed: {e}")

        rotate_deg = self._detect_rotation_deg(img)
        img, applied_deg, rot_warning = self._apply_rotation(img, rotate_deg)
        if rot_warning:
            warnings.append(rot_warning)

        try:
            data = pytesseract.image_to_data(
                img, output_type=Output.DICT, lang=lang
            )
        except Exception as e:
            if lang != "eng":
                warnings.append(
                    f"OCR with Tesseract language '{lang}' failed; "
                    "retried with English fallback."
                )
                try:
                    data = pytesseract.image_to_data(
                        img, output_type=Output.DICT, lang="eng"
                    )
                except Exception as retry_e:
                    print(f"[OCR] page {page_idx + 1} failed: {retry_e}")
                    return {
                        "page": page_idx,
                        "width": page_w_pt,
                        "height": page_h_pt,
                        "blocks": [],
                        "ocr_error": f"{e}; English fallback also failed: {retry_e}",
                    }
            else:
                print(f"[OCR] page {page_idx + 1} failed: {e}")
                return {
                    "page": page_idx,
                    "width": page_w_pt,
                    "height": page_h_pt,
                    "blocks": [],
                    "ocr_error": str(e),
                }

        page_layout = self._build_page_layout(
            page_idx, page_w_pt, page_h_pt, data, scale
        )
        if applied_deg == 180:
            page_layout = self._flip_page_layout_180(page_layout)
        if warnings:
            page_layout["ocr_warning"] = "; ".join(warnings)
        return page_layout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_pdf_text_and_layout(
        self,
        pdf_path: str,
        languages: Optional[Union[str, Sequence[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """OCR every page of a scanned PDF.

        Returns layout_data in the same shape as
        ExtractionService.extract_pdf_text_and_layout, but with `confidence`
        on each block and line.
        """
        self._ensure_available()
        lang, language_warnings = self.resolve_tesseract_language(languages)

        import fitz  # PyMuPDF
        from PIL import Image

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

                pages_data.append(self._ocr_image(
                    img, page_idx, page_w_pt, page_h_pt, scale,
                    lang=lang,
                    language_warnings=language_warnings if page_idx == 0 else None,
                ))
        finally:
            doc.close()

        return pages_data

    def extract_image_text_and_layout(
        self,
        image_path: str,
        languages: Optional[Union[str, Sequence[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """OCR a single image file (treated as a one-page document)."""
        self._ensure_available()
        lang, language_warnings = self.resolve_tesseract_language(languages)

        from PIL import Image

        img = Image.open(image_path)
        if img.mode != "RGB":
            img = img.convert("RGB")

        # The image has no intrinsic PDF page size; assume `self.dpi` so that
        # bboxes can be reported in points consistently.
        page_w_pt = img.width * 72.0 / float(self.dpi)
        page_h_pt = img.height * 72.0 / float(self.dpi)
        scale = 72.0 / float(self.dpi)

        return [
            self._ocr_image(
                img, 0, page_w_pt, page_h_pt, scale,
                lang=lang,
                language_warnings=language_warnings,
            )
        ]

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
