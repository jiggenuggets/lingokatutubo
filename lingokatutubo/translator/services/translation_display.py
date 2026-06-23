"""Display-safe translation helpers for previews and generated documents."""

from typing import Any

from .display_utils import clean_invisible_unicode
from .translation_dataset import UNKNOWN_FOR_REVIEW


RAW_REVIEW_MARKERS = (
    UNKNOWN_FOR_REVIEW,
    "UNKNOWN_FOR_REVIEW",
    "byt5_unavailable",
    "untranslated_needs_review",
)

SOURCE_DISPLAY_METHODS = {
    "unknown_for_review",
    "untranslated_needs_review",
    "byt5_unavailable",
    "untranslated",
}


def contains_review_marker(value: Any) -> bool:
    text = clean_invisible_unicode(value)
    if not text:
        return False
    normalized = text.lower()
    return any(marker.lower() in normalized for marker in RAW_REVIEW_MARKERS)


def is_source_display_method(value: Any) -> bool:
    return clean_invisible_unicode(value).strip().lower() in SOURCE_DISPLAY_METHODS


def is_display_fallback_translation(
    translated_text: Any,
    method: Any = "",
    cascade_stage: Any = "",
) -> bool:
    translated = clean_invisible_unicode(translated_text).strip()
    return (
        not translated
        or contains_review_marker(translated)
        or is_source_display_method(method)
        or is_source_display_method(cascade_stage)
    )


def get_display_translation_text(
    source_text: Any,
    translated_text: Any,
    method: Any = "",
    cascade_stage: Any = "",
) -> str:
    source = clean_invisible_unicode(source_text)
    translated = clean_invisible_unicode(translated_text)
    if is_display_fallback_translation(translated_text, method, cascade_stage):
        return source
    return translated


def get_display_translation(segment: Any) -> str:
    if isinstance(segment, dict):
        source_text = segment.get("source_text") or segment.get("original_text") or segment.get("original")
        translated_text = segment.get("translated_text") or segment.get("translated")
        method = segment.get("method") or segment.get("translation_method")
        cascade_stage = segment.get("cascade_stage")
    else:
        source_text = getattr(segment, "source_text", "")
        translated_text = getattr(segment, "translated_text", "")
        method = getattr(segment, "method", "")
        cascade_stage = ""

    return get_display_translation_text(
        source_text,
        translated_text,
        method,
        cascade_stage,
    )
