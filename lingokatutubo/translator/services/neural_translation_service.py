"""Experimental ByT5 Bagobo-Tagabawa -> English neural fallback.

This service is **disabled by default**. It exists only as an experimental
fallback for segments the phrasebook/dataset cascade could not translate, and
only for the Bagobo-Tagabawa -> English direction.

Hard guarantees enforced here (see CLAUDE.md and the integration brief):
  * Disabled unless ``LINGOKATUTUBO_NEURAL_TRANSLATION_ENABLED`` is true.
  * Only consulted for unmatched segments — it never overrides the phrasebook.
  * Only the configured source -> target direction (default Bagobo-Tagabawa ->
    English); any other direction returns ``None``.
  * Every produced result is marked ``needs_review=True`` with a fixed
    experimental warning and ``confidence=None`` (no fabricated score).
  * Model loading is lazy and **never raises into the pipeline**. If torch /
    transformers are missing or the model directory is unavailable, the
    service disables itself and the caller falls back to phrasebook behaviour.
  * The on-disk model path is never returned to callers/users.

Nothing in this module imports torch/transformers at import time, so the
Django app (and its tests) load without the heavyweight neural dependencies.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from django.conf import settings

from .display_utils import clean_invisible_unicode, safe_print
from .translation_dataset import _normalize_lang

# Public contract values — kept in one place so the pipeline and tests agree.
NEURAL_METHOD = "byt5_neural"
NEURAL_REVIEW_WARNING = "Experimental AI translation; needs language expert review."

# ByT5 is byte-level; cap source/target so a pathological line cannot blow up
# the byte sequence length. Matches the training configuration (512/512).
_TASK_PREFIX = "translate Bagobo-Tagabawa to English: "
_MAX_SOURCE_LENGTH = 512
_MAX_TARGET_LENGTH = 512

# Fallback defaults if the corresponding Django settings are absent (e.g. in
# a bare unit test context). Normal runs read these from settings instead.
_DEFAULT_SOURCE_LANG = "tagabawa"
_DEFAULT_TARGET_LANG = "english"
_DEFAULT_MODEL_DIR = "model_artifacts/byt5_tagabawa_english_full_v1"


def _harden_cpu_threading() -> None:
    """Cap OpenMP/MKL threads before torch is imported.

    Mirrors model_training/pipeline_utils.harden_cpu_threading. Inlined to keep
    the Django runtime independent of the research package (which is not an
    importable package — it has no __init__.py). Must run before ``import
    torch`` for the env vars to take effect.
    """
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class NeuralTranslationService:
    """Lazy, fail-safe wrapper around the experimental ByT5 checkpoint."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._load_attempted = False
        self._model: Any = None
        self._tokenizer: Any = None
        self._device: str = "cpu"
        # Surfaced to the pipeline so a load failure becomes a visible warning
        # rather than a silent disable.
        self.load_warning: Optional[str] = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """True only when the operator has explicitly opted in."""
        return bool(
            getattr(settings, "LINGOKATUTUBO_NEURAL_TRANSLATION_ENABLED", False)
        )

    def _model_dir(self) -> str:
        return str(
            getattr(settings, "LINGOKATUTUBO_BYT5_MODEL_DIR", _DEFAULT_MODEL_DIR)
        )

    def _configured_source_lang(self) -> str:
        return _normalize_lang(
            getattr(
                settings, "LINGOKATUTUBO_BYT5_SOURCE_LANGUAGE", _DEFAULT_SOURCE_LANG
            )
        )

    def _configured_target_lang(self) -> str:
        return _normalize_lang(
            getattr(
                settings, "LINGOKATUTUBO_BYT5_TARGET_LANGUAGE", _DEFAULT_TARGET_LANG
            )
        )

    def _is_supported_direction(self, source_lang: str, target_lang: str) -> bool:
        return (
            _normalize_lang(source_lang) == self._configured_source_lang()
            and _normalize_lang(target_lang) == self._configured_target_lang()
        )

    def is_available(self) -> bool:
        """Enabled, supported deps present, and the model actually loaded."""
        return self.is_enabled() and self._ensure_loaded()

    # ------------------------------------------------------------------
    # Loading (lazy, fail-safe)
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        """Attempt a one-time lazy load. Never raises.

        Returns True when a usable model/tokenizer is resident in memory.
        """
        if self._model is not None and self._tokenizer is not None:
            return True
        if self._load_attempted:
            # Already tried once and failed; stay disabled without retrying.
            return False

        with self._lock:
            # Re-check inside the lock (another thread may have loaded it).
            if self._model is not None and self._tokenizer is not None:
                return True
            if self._load_attempted:
                return False
            self._load_attempted = True
            self._load_model()

        return self._model is not None and self._tokenizer is not None

    def _load_model(self) -> None:
        model_dir = self._model_dir()

        if not os.path.isdir(model_dir) or not os.path.exists(
            os.path.join(model_dir, "config.json")
        ):
            # Don't leak the path to end users, but a backend log is useful.
            self.load_warning = (
                "Neural translation model is unavailable (model files not "
                "found); falling back to the phrasebook/dataset translator."
            )
            safe_print(
                f"[Neural] Model directory not usable: {model_dir!r}. "
                "Neural fallback disabled for this run."
            )
            return

        try:
            _harden_cpu_threading()
            import torch  # noqa: F401  (imported for thread cap + device select)
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            torch.set_num_threads(1)
            device = "cuda" if torch.cuda.is_available() else "cpu"

            tokenizer = AutoTokenizer.from_pretrained(model_dir)
            model = AutoModelForSeq2SeqLM.from_pretrained(model_dir).to(device)
            model.eval()
            # The checkpoint was saved with use_cache=False (training); enable
            # the KV cache for generation so CPU inference is not needlessly slow.
            try:
                model.config.use_cache = True
            except Exception:
                pass

            self._tokenizer = tokenizer
            self._model = model
            self._device = device
            self.load_warning = None
            safe_print(f"[Neural] ByT5 fallback model loaded on {device}.")
        except ImportError as exc:
            self.load_warning = (
                "Neural translation dependencies are not installed; falling "
                "back to the phrasebook/dataset translator."
            )
            safe_print(f"[Neural] Neural deps missing ({exc}); fallback disabled.")
        except Exception as exc:  # noqa: BLE001 - must never crash the job.
            self.load_warning = (
                "Neural translation model failed to load; falling back to the "
                "phrasebook/dataset translator."
            )
            safe_print(f"[Neural] Model load failed ({exc}); fallback disabled.")

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def translate_unmatched(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> Optional[Dict[str, Any]]:
        """Translate a single unmatched segment, or return ``None``.

        Returns ``None`` (so the caller keeps the phrasebook result) when:
          * neural is disabled,
          * the direction does not match the configured source/target language,
          * the text is empty,
          * the model could not be loaded, or
          * generation produced empty output / raised.

        On success returns a translation record dict compatible with the
        pipeline's expectations, always flagged for expert review.
        """
        source_text = clean_invisible_unicode(text).strip()
        if not source_text:
            return None
        if not self.is_enabled():
            return None
        if not self._is_supported_direction(source_lang, target_lang):
            return None
        if not self._ensure_loaded():
            return None

        try:
            prompt = f"{_TASK_PREFIX}{source_text}"
            inputs = self._tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=_MAX_SOURCE_LENGTH,
            ).to(self._device)
            generated_ids = self._model.generate(
                **inputs,
                max_length=_MAX_TARGET_LENGTH,
            )
            generated_text = clean_invisible_unicode(
                self._tokenizer.decode(
                    generated_ids[0],
                    skip_special_tokens=True,
                )
            ).strip()
        except Exception as exc:  # noqa: BLE001 - inference must never crash a job.
            safe_print(f"[Neural] Inference failed; keeping phrasebook result ({exc}).")
            return None

        if not generated_text:
            return None

        return _neural_result(generated_text)


def _neural_result(translated: str) -> Dict[str, Any]:
    """Build the experimental neural translation record.

    Every neural output carries method=byt5_neural, confidence=None,
    needs_review=True, and the fixed experimental warning.
    """
    return {
        "translated": translated,
        "method": NEURAL_METHOD,
        "cascade_stage": NEURAL_METHOD,
        "confidence": None,
        "needs_review": True,
        "warning": NEURAL_REVIEW_WARNING,
    }


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_neural_service: Optional[NeuralTranslationService] = None


def get_neural_translation_service() -> NeuralTranslationService:
    global _neural_service
    if _neural_service is None:
        _neural_service = NeuralTranslationService()
    return _neural_service
