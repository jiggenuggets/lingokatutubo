"""Text normalization helpers for user-facing output and saved artifacts."""

import sys
from typing import Any


INVISIBLE_UNICODE_REPLACEMENTS = {
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
    "\ufeff": "",
    "\u2060": "",
}


def clean_invisible_unicode(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    for bad, replacement in INVISIBLE_UNICODE_REPLACEMENTS.items():
        text = text.replace(bad, replacement)
    return text


def safe_print(*args: Any, **kwargs: Any) -> None:
    """Console-print that never raises ``UnicodeEncodeError``.

    Windows consoles often report a legacy 'charmap' codec (e.g. cp1252) that
    cannot represent many Unicode characters present in extracted/translated
    document text (zero-width spaces, BOM markers, OCR glyphs, etc). A crash
    here would otherwise propagate out of backend services and surface as a
    raw codec error to end users. Invisible characters are stripped first;
    anything else the console still can't encode is replaced rather than
    allowed to raise.
    """
    sep = kwargs.pop("sep", " ")
    end = kwargs.pop("end", "\n")
    file = kwargs.pop("file", None) or sys.stdout
    message = sep.join(clean_invisible_unicode(arg) for arg in args)
    try:
        file.write(message + end)
    except UnicodeEncodeError:
        encoding = getattr(file, "encoding", None) or "utf-8"
        safe_message = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        file.write(safe_message + end)
    try:
        file.flush()
    except Exception:
        pass
