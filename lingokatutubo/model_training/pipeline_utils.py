"""Shared, dependency-light helpers for the experimental ByT5 pipeline.

This module intentionally avoids importing torch/transformers at module
level so that `model_training/tests/` can exercise dataset validation,
splitting, prompt formatting, and device-selection logic quickly, without
downloading a model or requiring a GPU.

This pipeline is experimental. Nothing here is wired into the Django app.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd


def harden_cpu_threading() -> None:
    """Work around a reproducible OpenMP/MKL segfault seen on this CPU build
    when torch's default multi-threaded CPU kernels run alongside
    transformers/tokenizers. Must be called BEFORE `import torch` for the
    env vars to take effect; safe to call unconditionally (a GPU run's
    compute happens on-device, so capping CPU-side OpenMP threads here
    doesn't meaningfully slow it down).
    """
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

REQUIRED_COLUMNS = ["record_id", "bagobo_text", "english_text", "tagalog_text", "cebuano_text"]
TASK_PREFIX = "translate Bagobo-Tagabawa to English: "
REPLACEMENT_CHAR = "�"

GROUPING_COLUMN_CANDIDATES = ["source_document", "story_id", "chapter", "paragraph_group"]

EXPERIMENTAL_SPLIT_WARNING = (
    "No source/story/chapter/paragraph grouping metadata was found in this "
    "dataset, so this is a row-level 'experimental_row_split', NOT a "
    "leakage-safe split by document or narrative. Sentences from the same "
    "story may appear in both train and test. HIGH PRIORITY: a source-based "
    "split must be constructed (e.g. by tagging each row with its narrative "
    "title) before this split is used for final thesis-level evaluation."
)


class DatasetValidationError(ValueError):
    """Raised when the input CSV fails a required structural check."""


def load_dataset_csv(path: str | Path) -> pd.DataFrame:
    """Load the cleaned CSV as plain strings, preserving original orthography.

    Uses utf-8-sig (tolerates a BOM) and disables NA sniffing so that
    accented Bagobo-Tagabawa text and empty-string cells are not mangled.
    """
    path = Path(path)
    if not path.exists():
        raise DatasetValidationError(f"Dataset CSV not found at: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False, na_filter=False)
    return df


def find_grouping_column(df: pd.DataFrame) -> Optional[str]:
    for candidate in GROUPING_COLUMN_CANDIDATES:
        if candidate in df.columns:
            return candidate
    return None


@dataclass
class ValidationReport:
    total_rows: int
    missing_columns: list[str] = field(default_factory=list)
    duplicate_record_ids: dict[str, int] = field(default_factory=dict)
    blank_bagobo_count: int = 0
    blank_english_count: int = 0
    exact_duplicate_pair_rows: list[str] = field(default_factory=list)
    copy_through_rows: list[str] = field(default_factory=list)
    replacement_char_rows: list[str] = field(default_factory=list)
    usable_record_count: int = 0
    grouping_column: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "missing_columns": self.missing_columns,
            "duplicate_record_ids": self.duplicate_record_ids,
            "blank_bagobo_count": self.blank_bagobo_count,
            "blank_english_count": self.blank_english_count,
            "exact_duplicate_pair_rows": self.exact_duplicate_pair_rows,
            "copy_through_rows": self.copy_through_rows,
            "replacement_char_rows": self.replacement_char_rows,
            "usable_record_count": self.usable_record_count,
            "grouping_column": self.grouping_column,
        }


def validate_dataset(df: pd.DataFrame) -> ValidationReport:
    """Run the Step 1 structural checks. Raises on missing required columns."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DatasetValidationError(
            f"Dataset is missing required column(s): {missing}. "
            f"Required columns: {REQUIRED_COLUMNS}"
        )

    report = ValidationReport(total_rows=len(df), missing_columns=[])
    report.grouping_column = find_grouping_column(df)

    id_counts = Counter(df["record_id"])
    report.duplicate_record_ids = {k: v for k, v in id_counts.items() if v > 1}

    bagobo = df["bagobo_text"].astype(str).str.strip()
    english = df["english_text"].astype(str).str.strip()

    report.blank_bagobo_count = int((bagobo == "").sum())
    report.blank_english_count = int((english == "").sum())

    pair_counts = Counter(zip(bagobo, english))
    dupe_pairs = {pair for pair, count in pair_counts.items() if count > 1}
    report.exact_duplicate_pair_rows = [
        rid
        for rid, b, e in zip(df["record_id"], bagobo, english)
        if (b, e) in dupe_pairs
    ]

    report.copy_through_rows = [
        rid for rid, b, e in zip(df["record_id"], bagobo, english) if b != "" and b == e
    ]

    def _has_replacement_char(row) -> bool:
        return any(REPLACEMENT_CHAR in str(row[c]) for c in REQUIRED_COLUMNS if c in row)

    report.replacement_char_rows = [
        rid for rid, has_bad in zip(df["record_id"], df.apply(_has_replacement_char, axis=1)) if has_bad
    ]

    usable_mask = (bagobo != "") & (english != "")
    report.usable_record_count = int(usable_mask.sum())

    return report


def clean_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, ValidationReport]:
    """Validate, then drop only confirmed-bad rows: blanks, exact duplicate
    (bagobo, english) pairs (keep first occurrence), and copy-through rows
    where the "translation" is just the source text repeated verbatim.

    Original orthography is preserved verbatim for every kept row.
    """
    report = validate_dataset(df)

    bagobo = df["bagobo_text"].astype(str).str.strip()
    english = df["english_text"].astype(str).str.strip()

    keep = (bagobo != "") & (english != "") & (bagobo != english)
    cleaned = df.loc[keep].copy()

    cleaned = cleaned.drop_duplicates(subset=["bagobo_text", "english_text"], keep="first")

    report.usable_record_count = len(cleaned)
    return cleaned.reset_index(drop=True), report


@dataclass
class SplitResult:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    manifest: dict


def split_experimental_row_level(
    df: pd.DataFrame,
    seed: int = 42,
    train_frac: float = 0.8,
    val_frac: float = 0.1,
) -> SplitResult:
    """Deterministic 80/10/10 split with a fixed seed.

    This is row-level because the dataset carries no source/story/chapter
    grouping column. It is intentionally labeled 'experimental_row_split'
    rather than presented as leakage-safe. Exact-duplicate (bagobo, english)
    pairs must already have been removed by `clean_dataset` before calling
    this, so no duplicate pair can appear in more than one split.
    """
    from sklearn.model_selection import train_test_split

    if abs(train_frac + val_frac) >= 1.0:
        raise ValueError("train_frac + val_frac must leave a positive remainder for the test split")

    record_ids = df["record_id"].tolist()
    if len(set(record_ids)) != len(record_ids):
        raise DatasetValidationError("record_id must be unique before splitting")

    test_frac = 1.0 - train_frac - val_frac

    train_val_df, test_df = train_test_split(df, test_size=test_frac, random_state=seed, shuffle=True)
    relative_val_frac = val_frac / (train_frac + val_frac)
    train_df, val_df = train_test_split(train_val_df, test_size=relative_val_frac, random_state=seed, shuffle=True)

    train_df = train_df.reset_index(drop=True)
    val_df = val_df.reset_index(drop=True)
    test_df = test_df.reset_index(drop=True)

    manifest = {
        "split_method": "experimental_row_split",
        "leakage_safe": False,
        "warning": EXPERIMENTAL_SPLIT_WARNING,
        "seed": seed,
        "ratios": {"train": train_frac, "validation": val_frac, "test": test_frac},
        "counts": {"train": len(train_df), "validation": len(val_df), "test": len(test_df)},
        "record_id_overlap_check": _no_id_overlap(train_df, val_df, test_df),
    }
    return SplitResult(train=train_df, validation=val_df, test=test_df, manifest=manifest)


def _no_id_overlap(*dfs: pd.DataFrame) -> bool:
    seen: set[str] = set()
    for d in dfs:
        ids = set(d["record_id"])
        if seen & ids:
            return False
        seen |= ids
    return True


def to_model_records(df: pd.DataFrame) -> list[dict]:
    """Convert a cleaned split dataframe into the model-ready JSONL schema."""
    return [
        {
            "record_id": row["record_id"],
            "source_text": row["bagobo_text"],
            "target_text": row["english_text"],
        }
        for _, row in df.iterrows()
    ]


def write_jsonl(records: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def build_prefixed_input(source_text: str, prefix: str = TASK_PREFIX) -> str:
    """Apply the fixed task prefix ByT5 needs to know which direction to translate."""
    return f"{prefix}{source_text}"


def select_device() -> tuple[str, Optional[str]]:
    """Pick a device for training/inference without ever raising.

    Returns (device_str, warning_or_None). Lazily imports torch so this
    function (and anything that only needs the CPU-fallback branch) can be
    exercised in tests without torch installed.
    """
    try:
        import torch
    except ImportError:
        return "cpu", "torch is not installed; defaulting to CPU."

    if torch.cuda.is_available():
        return "cuda", None

    return (
        "cpu",
        "CUDA is not available; training will run on CPU and may be very slow. "
        "For a full run, consider Google Colab with a GPU runtime "
        "(Runtime > Change runtime type > GPU), clone/upload this repo, "
        "`pip install -r requirements-training.txt`, then run the same "
        "train_byt5.py command.",
    )


def resolve_model_dir(path: str | Path) -> Path:
    """Validate a model directory exists before attempting to load it.

    Raises FileNotFoundError with an actionable message rather than letting
    transformers raise an opaque error deep in `from_pretrained`.
    """
    model_dir = Path(path)
    if not model_dir.exists() or not model_dir.is_dir():
        raise FileNotFoundError(
            f"Model directory not found: {model_dir}. "
            "Run `python model_training/train_byt5.py` first to produce a "
            "fine-tuned model, or pass --model-dir pointing at a valid "
            "checkpoint directory."
        )
    if not (model_dir / "config.json").exists():
        raise FileNotFoundError(
            f"'{model_dir}' exists but does not look like a saved model "
            "(no config.json found). Point --model-dir at the directory "
            "produced by train_byt5.py's trainer.save_model(...)."
        )
    return model_dir


def format_prediction(source_text: str, generated_text: str, model_version: str) -> dict:
    """Structure a single prediction result. Never fabricates a confidence score."""
    return {
        "source_text": source_text,
        "generated_text": generated_text,
        "model_version": model_version,
        "needs_review": True,
        "note": "Experimental machine translation. Expert review required before any use.",
    }


def compute_basic_eval_stats(sources: list[str], predictions: list[str], references: list[str]) -> dict:
    """Pure-python evaluation signals that don't require sacrebleu/torch."""
    n = len(predictions)
    if n == 0:
        return {
            "exact_match_rate": 0.0,
            "empty_output_rate": 0.0,
            "source_copy_rate": 0.0,
            "average_generated_length_chars": 0.0,
            "duplicate_prediction_rate": 0.0,
        }

    exact_matches = sum(1 for p, r in zip(predictions, references) if p.strip() == r.strip())
    empty_outputs = sum(1 for p in predictions if p.strip() == "")
    source_copies = sum(1 for s, p in zip(sources, predictions) if p.strip() == s.strip())
    avg_len = sum(len(p) for p in predictions) / n

    pred_counts = Counter(p.strip() for p in predictions)
    duplicated = sum(count for count in pred_counts.values() if count > 1)

    return {
        "exact_match_rate": exact_matches / n,
        "empty_output_rate": empty_outputs / n,
        "source_copy_rate": source_copies / n,
        "average_generated_length_chars": avg_len,
        "duplicate_prediction_rate": duplicated / n,
    }
