"""
Tesseract environment detection for fail-early, honest reporting.

Never masks the absence of Tesseract with silent fallback behaviour.
Call check_tesseract_environment() before running OCR to surface any gap
that would otherwise produce empty or wrong output with no visible error.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List


def check_tesseract_environment() -> Dict[str, Any]:
    """Probe the Tesseract environment and return a structured status dict.

    Returns a dict with the following keys:
        available       bool  — True only when Tesseract binary is callable
        version         str | None
        languages       list[str] — installed language pack names (e.g. ['eng', 'osd'])
        osd_available   bool  — True when osd.traineddata is present
        binary_path     str | None — value of TESSERACT_CMD env var if set
        errors          list[str] — human-readable problems found during probe

    Never raises — all exceptions are caught and turned into entries in errors[].
    """
    result: Dict[str, Any] = {
        "available": False,
        "version": None,
        "languages": [],
        "osd_available": False,
        "binary_path": None,
        "errors": [],
    }

    def add_error(msg: str) -> None:
        if msg not in result["errors"]:
            result["errors"].append(msg)

    # Honour an explicit binary path when the user provides one.
    tess_cmd = os.environ.get("TESSERACT_CMD")
    if tess_cmd:
        result["binary_path"] = tess_cmd
        if not os.path.isfile(tess_cmd):
            add_error(
                f"TESSERACT_CMD is set to '{tess_cmd}' "
                "but that file does not exist."
            )

    # pytesseract must be importable.
    try:
        import pytesseract as _pt
        if tess_cmd:
            _pt.pytesseract.tesseract_cmd = tess_cmd
    except ImportError:
        add_error(
            "pytesseract is not installed. "
            "Run: pip install pytesseract"
        )
        return result

    # Attempt to call the Tesseract binary.
    try:
        version = _pt.get_tesseract_version()
        result["version"] = str(version)
        result["available"] = True
    except Exception as exc:
        add_error(
            f"Tesseract binary not found or not executable: {exc}. "
            "Install Tesseract from https://github.com/tesseract-ocr/tesseract "
            "and add it to PATH, or set the TESSERACT_CMD environment variable "
            "to the full path of the tesseract executable."
        )
        return result

    # Enumerate installed language packs.
    try:
        languages: List[str] = [str(lang) for lang in _pt.get_languages(config="")]
        result["languages"] = languages
        result["osd_available"] = "osd" in languages

        if not result["osd_available"]:
            add_error(
                "osd.traineddata is not installed. "
                "Orientation/script detection will be disabled. "
                "Copy osd.traineddata into the Tesseract tessdata directory."
            )

        if "eng" not in languages:
            add_error(
                "English language pack 'eng' is not installed. "
                "OCR will fail until eng.traineddata is added to tessdata."
            )

    except Exception as exc:
        add_error(f"Could not enumerate Tesseract language packs: {exc}")

    return result


def assert_tesseract_available() -> None:
    """Raise RuntimeError with a clear message when Tesseract is not usable.

    Use this at the entry point of any code path that unconditionally needs
    Tesseract so the caller gets an actionable error instead of a cryptic
    failure deep inside pytesseract.
    """
    env = check_tesseract_environment()
    if not env["available"]:
        errors_text = (
            "; ".join(env["errors"])
            if env["errors"]
            else "Tesseract executable not found."
        )
        raise RuntimeError(
            "Tesseract OCR is not available in this environment. "
            f"{errors_text}"
        )


def format_environment_report(env: Dict[str, Any]) -> str:
    """Return a human-readable multi-line summary of check_tesseract_environment()."""
    lines = [
        "=== Tesseract Environment Report ===",
        f"  Available      : {env['available']}",
        f"  Version        : {env['version'] or 'N/A'}",
        f"  Binary path    : {env['binary_path'] or '(from PATH)'}",
        f"  OSD available  : {env['osd_available']}",
        f"  Languages      : {', '.join(env['languages']) or 'none'}",
    ]
    if env["errors"]:
        lines.append("  Errors:")
        for err in env["errors"]:
            lines.append(f"    - {err}")
    else:
        lines.append("  No errors detected.")
    lines.append("=" * 38)
    return "\n".join(lines)
