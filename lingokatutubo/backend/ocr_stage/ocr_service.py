"""Compatibility wrapper for migrated Django translator OCR services."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import _django_compat  # noqa: F401,E402
from translator.services.ocr_stage.ocr_service import *  # noqa: F401,F403
