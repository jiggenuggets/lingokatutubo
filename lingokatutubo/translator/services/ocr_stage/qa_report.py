"""
OCR quality metrics: Character Error Rate (CER), Word Error Rate (WER),
reading-order audit, and document-level QA report generation.

All metric functions are pure Python and require no external dependencies.
The QA report classes aggregate per-page results into a structured summary
suitable for surfacing on the /preview page or writing to structure.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ------------------------------------------------------------------
# Levenshtein edit distance (characters and word sequences)
# ------------------------------------------------------------------

def _edit_distance_chars(ref: str, hyp: str) -> int:
    """Levenshtein edit distance between two character strings."""
    m, n = len(ref), len(hyp)
    if m == 0:
        return n
    if n == 0:
        return m
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            tmp = dp[j]
            dp[j] = prev if ref[i - 1] == hyp[j - 1] else 1 + min(prev, dp[j], dp[j - 1])
            prev = tmp
    return dp[n]


def _edit_distance_words(ref: list, hyp: list) -> int:
    """Levenshtein edit distance between two word sequences."""
    m, n = len(ref), len(hyp)
    if m == 0:
        return n
    if n == 0:
        return m
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            tmp = dp[j]
            dp[j] = prev if ref[i - 1] == hyp[j - 1] else 1 + min(prev, dp[j], dp[j - 1])
            prev = tmp
    return dp[n]


# ------------------------------------------------------------------
# Public metric functions
# ------------------------------------------------------------------

def calculate_cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate = edit_distance(ref_chars, hyp_chars) / len(ref_chars).

    Spaces are stripped before comparison (standard OCR/ASR convention).
    Returns 0.0 when reference is empty. Result is capped at 1.0.
    """
    ref = reference.replace(" ", "")
    hyp = hypothesis.replace(" ", "")
    if not ref:
        return 0.0
    return min(1.0, _edit_distance_chars(ref, hyp) / len(ref))


def calculate_wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate = edit_distance(ref_words, hyp_words) / len(ref_words).

    Returns 0.0 when reference contains no words. Result is capped at 1.0.
    """
    ref_words = reference.split()
    hyp_words = hypothesis.split()
    if not ref_words:
        return 0.0
    return min(1.0, _edit_distance_words(ref_words, hyp_words) / len(ref_words))


# ------------------------------------------------------------------
# Reading-order audit
# ------------------------------------------------------------------

# X fraction of page width used to identify left/right column boundaries.
# A block whose x1 is at or below this fraction is considered "left column".
_COLUMN_SPLIT_FRACTION: float = 0.45


def audit_reading_order(layout_data: List[Dict[str, Any]]) -> List[str]:
    """Inspect block order in every page and return a list of issue strings.

    Per-page checks:
    - Empty pages (no text blocks) are noted.
    - Text blocks are expected to appear in top-to-bottom order.
      A 5-point Y tolerance is allowed to accommodate side-by-side columns.
    - Pages with two spatially distinct X zones are flagged as two-column
      layouts so callers know to verify column reading order independently.
    """
    issues: List[str] = []

    for page_data in layout_data:
        page_num = page_data.get("page", 0) + 1
        page_w = float(page_data.get("width") or 0)

        text_blocks = [
            b for b in page_data.get("blocks", [])
            if b.get("type") == "text"
            and isinstance(b.get("bbox"), list)
            and len(b["bbox"]) == 4
        ]

        if not text_blocks:
            issues.append(
                f"Page {page_num}: no text blocks "
                "(blank page or extraction failed)."
            )
            continue

        # --- Two-column detection ----------------------------------------
        if page_w > 0:
            left_limit = page_w * _COLUMN_SPLIT_FRACTION
            right_limit = page_w * (1.0 - _COLUMN_SPLIT_FRACTION)
            left_blocks = [b for b in text_blocks if b["bbox"][2] <= left_limit]
            right_blocks = [b for b in text_blocks if b["bbox"][0] >= right_limit]
            if left_blocks and right_blocks:
                issues.append(
                    f"Page {page_num}: two-column layout detected "
                    f"({len(left_blocks)} left-column block(s), "
                    f"{len(right_blocks)} right-column block(s)). "
                    "Verify left column is read before right column."
                )

        # --- Top-to-bottom order check -----------------------------------
        y_tops = [float(b["bbox"][1]) for b in text_blocks]
        for i in range(1, len(y_tops)):
            if y_tops[i] < y_tops[i - 1] - 5.0:
                issues.append(
                    f"Page {page_num}: block {i + 1} (y0={y_tops[i]:.1f}) "
                    f"appears above block {i} (y0={y_tops[i - 1]:.1f}); "
                    "possible reading-order problem."
                )

    return issues


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class PageQAResult:
    """Per-page quality metrics produced by build_document_qa_report."""

    page_index: int
    extraction_method: str       # "digital" | "ocr" | "failed"
    char_count: int
    block_count: int
    ocr_confidence: Optional[float] = None
    cer: Optional[float] = None
    wer: Optional[float] = None
    reading_order_issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    processing_time_s: Optional[float] = None


@dataclass
class DocumentQAReport:
    """Aggregated QA report for a single processed document.

    All fields are plain Python scalars so the report can be serialised
    to JSON via as_dict() and stored in structure.json or shown on /preview.
    """

    document_name: str
    extraction_method: str
    page_count: int
    ocr_confidence: Optional[float]   # mean across pages that have OCR confidence
    cer: Optional[float]               # mean CER across pages with ground truth
    wer: Optional[float]               # mean WER across pages with ground truth
    empty_page_count: int
    failed_page_count: int
    total_processing_time_s: float
    reading_order_issues: List[str]
    output_result: str                 # "ok" | "partial" | "failed"
    warnings: List[str]
    pages: List[PageQAResult]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "document_name": self.document_name,
            "extraction_method": self.extraction_method,
            "page_count": self.page_count,
            "ocr_confidence": self.ocr_confidence,
            "cer": round(self.cer, 4) if self.cer is not None else None,
            "wer": round(self.wer, 4) if self.wer is not None else None,
            "empty_page_rate": (
                round(self.empty_page_count / self.page_count, 4)
                if self.page_count else 0.0
            ),
            "failed_page_count": self.failed_page_count,
            "total_processing_time_s": round(self.total_processing_time_s, 3),
            "reading_order_issues": self.reading_order_issues,
            "output_result": self.output_result,
            "warnings": self.warnings,
            "pages": [
                {
                    "page_index": p.page_index,
                    "extraction_method": p.extraction_method,
                    "char_count": p.char_count,
                    "block_count": p.block_count,
                    "ocr_confidence": p.ocr_confidence,
                    "cer": round(p.cer, 4) if p.cer is not None else None,
                    "wer": round(p.wer, 4) if p.wer is not None else None,
                    "reading_order_issues": p.reading_order_issues,
                    "warnings": p.warnings,
                    "processing_time_s": (
                        round(p.processing_time_s, 3)
                        if p.processing_time_s is not None else None
                    ),
                }
                for p in self.pages
            ],
        }


# ------------------------------------------------------------------
# Report builder
# ------------------------------------------------------------------

def build_document_qa_report(
    document_name: str,
    layout_data: List[Dict[str, Any]],
    ground_truth_pages: Optional[List[str]] = None,
    extraction_method: str = "unknown",
    total_processing_time_s: float = 0.0,
    output_result: str = "ok",
    page_processing_times: Optional[List[float]] = None,
    warnings: Optional[List[str]] = None,
) -> DocumentQAReport:
    """Build a DocumentQAReport from layout_data, optionally with ground truth.

    Args:
        document_name: Human-readable label for the document.
        layout_data: List of page dicts in the standard pipeline format.
        ground_truth_pages: Optional ground-truth text strings, one per page.
            When provided CER and WER are computed per page and averaged.
        extraction_method: Overall method label ("digital", "ocr", "hybrid", …).
        total_processing_time_s: Wall-clock extraction time in seconds.
        output_result: "ok" | "partial" | "failed" — reconstruction outcome.
        page_processing_times: Optional per-page timing list (seconds).
        warnings: Optional document-level warning strings.
    """
    reading_order_issues = audit_reading_order(layout_data)
    page_results: List[PageQAResult] = []

    cer_values: List[float] = []
    wer_values: List[float] = []
    conf_values: List[float] = []
    empty_count = 0
    failed_count = 0

    for page_idx, page_data in enumerate(layout_data):
        text_blocks = [
            b for b in page_data.get("blocks", [])
            if b.get("type") == "text"
        ]
        char_count = sum(
            len(line.get("text", ""))
            for b in text_blocks
            for line in b.get("lines", [])
        )
        block_count = len(text_blocks)

        if char_count == 0:
            empty_count += 1
        if page_data.get("ocr_error"):
            failed_count += 1

        # OCR confidence: mean block confidence (scanned pages carry this)
        page_conf_values = [
            float(b["confidence"])
            for b in text_blocks
            if b.get("confidence") is not None
        ]
        page_conf = (
            sum(page_conf_values) / len(page_conf_values)
            if page_conf_values else None
        )
        if page_conf is not None:
            conf_values.append(page_conf)

        # CER / WER when ground truth is provided
        page_cer: Optional[float] = None
        page_wer: Optional[float] = None
        if ground_truth_pages and page_idx < len(ground_truth_pages):
            hyp_text = " ".join(
                line.get("text", "")
                for b in text_blocks
                for line in b.get("lines", [])
            ).strip()
            ref_text = ground_truth_pages[page_idx]
            page_cer = calculate_cer(ref_text, hyp_text)
            page_wer = calculate_wer(ref_text, hyp_text)
            cer_values.append(page_cer)
            wer_values.append(page_wer)

        # Per-page reading order issues
        page_issues = [
            issue for issue in reading_order_issues
            if issue.startswith(f"Page {page_idx + 1}:")
        ]

        page_warnings: List[str] = []
        if page_data.get("ocr_warning"):
            page_warnings.append(page_data["ocr_warning"])
        if page_data.get("ocr_error"):
            page_warnings.append(f"OCR error: {page_data['ocr_error']}")

        page_results.append(PageQAResult(
            page_index=page_idx,
            extraction_method=page_data.get("extraction_method", extraction_method),
            char_count=char_count,
            block_count=block_count,
            ocr_confidence=(
                round(page_conf, 4) if page_conf is not None else None
            ),
            cer=round(page_cer, 4) if page_cer is not None else None,
            wer=round(page_wer, 4) if page_wer is not None else None,
            reading_order_issues=page_issues,
            warnings=page_warnings,
            processing_time_s=(
                page_processing_times[page_idx]
                if page_processing_times and page_idx < len(page_processing_times)
                else None
            ),
        ))

    mean_conf = (
        round(sum(conf_values) / len(conf_values), 4) if conf_values else None
    )
    mean_cer = (
        round(sum(cer_values) / len(cer_values), 4) if cer_values else None
    )
    mean_wer = (
        round(sum(wer_values) / len(wer_values), 4) if wer_values else None
    )

    return DocumentQAReport(
        document_name=document_name,
        extraction_method=extraction_method,
        page_count=len(layout_data),
        ocr_confidence=mean_conf,
        cer=mean_cer,
        wer=mean_wer,
        empty_page_count=empty_count,
        failed_page_count=failed_count,
        total_processing_time_s=total_processing_time_s,
        reading_order_issues=reading_order_issues,
        output_result=output_result,
        warnings=warnings or [],
        pages=page_results,
    )


# ------------------------------------------------------------------
# PSM benchmark helper
# ------------------------------------------------------------------

def compare_psm_modes(
    ocr_service_cls,
    image,
    ground_truth: str,
    psm_values: Optional[List[int]] = None,
    lang: str = "eng",
    timeout: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Run OCR at multiple PSM values and return CER/WER for each.

    This is infrastructure for evidence-based PSM selection. Production
    default (PSM 3) should only be changed when a lower-CER mode is
    confirmed across a representative evaluation set.

    Returns a list of dicts: [{psm, hypothesis, cer, wer}, ...]
    """
    if psm_values is None:
        psm_values = [3, 4, 6]

    results = []
    for psm in psm_values:
        try:
            svc = ocr_service_cls(psm=psm, lang=lang, detect_orientation=False)
            # Use internal _ocr_image on the supplied PIL image
            page_layout = svc._ocr_image(
                image,
                page_idx=0,
                page_w_pt=float(image.width),
                page_h_pt=float(image.height),
                scale=1.0,
                lang=lang,
            )
            hyp = " ".join(
                line.get("text", "")
                for b in page_layout.get("blocks", [])
                for line in b.get("lines", [])
            ).strip()
            cer = calculate_cer(ground_truth, hyp)
            wer = calculate_wer(ground_truth, hyp)
        except Exception as exc:
            hyp = ""
            cer = 1.0
            wer = 1.0
            results.append({
                "psm": psm,
                "hypothesis": hyp,
                "cer": cer,
                "wer": wer,
                "error": str(exc),
            })
            continue

        results.append({
            "psm": psm,
            "hypothesis": hyp,
            "cer": round(cer, 4),
            "wer": round(wer, 4),
        })
    return results
