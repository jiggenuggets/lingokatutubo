"""Lightweight tests for the ByT5 pipeline helpers.

No model download, no GPU, no torch/transformers required to run these —
only pandas + scikit-learn (see requirements-training.txt). Run with the
training virtualenv active:

    python -m pytest model_training/tests -v

Intentionally NOT part of `python manage.py test` (the Django app's
environment does not have pandas/scikit-learn installed).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline_utils as pu  # noqa: E402

COLUMNS = ["record_id", "bagobo_text", "english_text", "tagalog_text", "cebuano_text"]


def make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLUMNS)


# ---------------------------------------------------------------------------
# Required CSV columns are validated
# ---------------------------------------------------------------------------

def test_missing_required_column_raises():
    df = pd.DataFrame([{"record_id": "SENT-0001", "bagobo_text": "x", "english_text": "y"}])
    with pytest.raises(pu.DatasetValidationError):
        pu.validate_dataset(df)


def test_all_required_columns_present_passes_validation():
    df = make_df(
        [
            {"record_id": "SENT-0001", "bagobo_text": "Madigár", "english_text": "Good", "tagalog_text": "", "cebuano_text": ""},
        ]
    )
    report = pu.validate_dataset(df)
    assert report.missing_columns == []
    assert report.usable_record_count == 1


# ---------------------------------------------------------------------------
# Blank pairs are rejected
# ---------------------------------------------------------------------------

def test_blank_bagobo_or_english_rows_are_dropped():
    df = make_df(
        [
            {"record_id": "SENT-0001", "bagobo_text": "Madigár", "english_text": "Good", "tagalog_text": "", "cebuano_text": ""},
            {"record_id": "SENT-0002", "bagobo_text": "", "english_text": "Missing source", "tagalog_text": "", "cebuano_text": ""},
            {"record_id": "SENT-0003", "bagobo_text": "Missing target", "english_text": "  ", "tagalog_text": "", "cebuano_text": ""},
        ]
    )
    cleaned, report = pu.clean_dataset(df)
    assert list(cleaned["record_id"]) == ["SENT-0001"]
    assert report.blank_bagobo_count == 1
    assert report.blank_english_count == 1


def test_copy_through_untranslated_row_is_dropped():
    df = make_df(
        [
            {"record_id": "SENT-0001", "bagobo_text": "Madigár", "english_text": "Good", "tagalog_text": "", "cebuano_text": ""},
            {"record_id": "SENT-0002", "bagobo_text": "Same text", "english_text": "Same text", "tagalog_text": "", "cebuano_text": ""},
        ]
    )
    cleaned, report = pu.clean_dataset(df)
    assert list(cleaned["record_id"]) == ["SENT-0001"]
    assert report.copy_through_rows == ["SENT-0002"]


# ---------------------------------------------------------------------------
# Duplicates do not cross splits / split files behave correctly
# ---------------------------------------------------------------------------

def _fixture_df(n: int = 40) -> pd.DataFrame:
    rows = [
        {
            "record_id": f"SENT-{i:04d}",
            "bagobo_text": f"Bagobo sentence number {i} áéíóú",
            "english_text": f"English sentence number {i}",
            "tagalog_text": "",
            "cebuano_text": "",
        }
        for i in range(n)
    ]
    return make_df(rows)


def test_exact_duplicate_pairs_removed_before_split_so_none_cross():
    df = _fixture_df(20)
    dup_row = df.iloc[0].copy()
    dup_row["record_id"] = "SENT-9999"
    df = pd.concat([df, pd.DataFrame([dup_row])], ignore_index=True)

    cleaned, report = pu.clean_dataset(df)
    # the later duplicate (SENT-9999) is dropped; the original (SENT-0000) is kept
    assert "SENT-9999" not in set(cleaned["record_id"])
    assert "SENT-0000" in set(cleaned["record_id"])
    pairs = list(zip(cleaned["bagobo_text"], cleaned["english_text"]))
    assert len(pairs) == len(set(pairs))

    split = pu.split_experimental_row_level(cleaned, seed=42)
    all_ids = list(split.train["record_id"]) + list(split.validation["record_id"]) + list(split.test["record_id"])
    assert len(all_ids) == len(set(all_ids))


def test_split_ids_do_not_overlap_and_files_are_created(tmp_path):
    df = _fixture_df(40)
    cleaned, _ = pu.clean_dataset(df)
    split = pu.split_experimental_row_level(cleaned, seed=42, train_frac=0.8, val_frac=0.1)

    assert split.manifest["record_id_overlap_check"] is True
    assert split.manifest["split_method"] == "experimental_row_split"
    assert split.manifest["leakage_safe"] is False

    train_ids = set(split.train["record_id"])
    val_ids = set(split.validation["record_id"])
    test_ids = set(split.test["record_id"])
    assert not (train_ids & val_ids)
    assert not (train_ids & test_ids)
    assert not (val_ids & test_ids)
    assert len(train_ids) + len(val_ids) + len(test_ids) == 40

    for name, frame in [("train", split.train), ("validation", split.validation), ("test", split.test)]:
        csv_path = tmp_path / f"{name}.csv"
        frame.to_csv(csv_path, index=False, encoding="utf-8")
        jsonl_path = tmp_path / f"{name}.jsonl"
        pu.write_jsonl(pu.to_model_records(frame), jsonl_path)
        assert csv_path.exists()
        assert jsonl_path.exists()


def test_split_is_deterministic_for_fixed_seed():
    df = _fixture_df(40)
    cleaned, _ = pu.clean_dataset(df)
    split_a = pu.split_experimental_row_level(cleaned, seed=42)
    split_b = pu.split_experimental_row_level(cleaned, seed=42)
    assert list(split_a.test["record_id"]) == list(split_b.test["record_id"])


# ---------------------------------------------------------------------------
# UTF-8 Tagabawa text survives export
# ---------------------------------------------------------------------------

def test_utf8_accented_text_survives_jsonl_export(tmp_path):
    accented = "Ágpanumbalé, Madigár, kéduh, Bagóbó, áskandin, Sandawà"
    df = make_df(
        [
            {"record_id": "SENT-0001", "bagobo_text": accented, "english_text": "An accented sentence", "tagalog_text": "", "cebuano_text": ""},
        ]
    )
    cleaned, _ = pu.clean_dataset(df)
    records = pu.to_model_records(cleaned)
    out_path = tmp_path / "out.jsonl"
    pu.write_jsonl(records, out_path)

    loaded = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
    assert loaded["source_text"] == accented


# ---------------------------------------------------------------------------
# Task prefix
# ---------------------------------------------------------------------------

def test_task_prefix_is_added_correctly():
    prompt = pu.build_prefixed_input("Madigár su kannun kó.")
    assert prompt == "translate Bagobo-Tagabawa to English: Madigár su kannun kó."
    assert prompt.startswith(pu.TASK_PREFIX)


# ---------------------------------------------------------------------------
# Prediction output marked needs_review
# ---------------------------------------------------------------------------

def test_prediction_marked_needs_review_true():
    result = pu.format_prediction("source", "generated", "v1")
    assert result["needs_review"] is True
    assert "confidence" not in result


def test_predict_run_prediction_uses_stub_model_and_marks_needs_review():
    """Mocks model loading entirely — proves predict_byt5's core logic
    doesn't need a real model to be exercised."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import predict_byt5

    class StubTensor(dict):
        def to(self, device):
            return self

    class StubTokenizer:
        def __call__(self, text, return_tensors=None):
            return StubTensor({"input_ids": [[1, 2, 3]]})

        def decode(self, ids, skip_special_tokens=True):
            return "stub english output"

    class StubModel:
        def generate(self, **kwargs):
            return [[1, 2, 3]]

    result = predict_byt5.run_prediction(StubModel(), StubTokenizer(), "cpu", "Bagobo text", "stub-model-v1")
    assert result["generated_text"] == "stub english output"
    assert result["needs_review"] is True
    assert result["model_version"] == "stub-model-v1"
    assert "confidence" not in result


# ---------------------------------------------------------------------------
# Missing model directory fails with a clear error
# ---------------------------------------------------------------------------

def test_missing_model_dir_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError) as exc_info:
        pu.resolve_model_dir(missing)
    assert "does_not_exist" in str(exc_info.value)
    assert "train_byt5.py" in str(exc_info.value)


def test_model_dir_without_config_json_raises_clear_error(tmp_path):
    empty_dir = tmp_path / "empty_model_dir"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError) as exc_info:
        pu.resolve_model_dir(empty_dir)
    assert "config.json" in str(exc_info.value)


# ---------------------------------------------------------------------------
# CPU selection when CUDA is absent
# ---------------------------------------------------------------------------

def test_select_device_falls_back_to_cpu_when_torch_missing():
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("simulated: torch not installed")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        device, warning = pu.select_device()
    assert device == "cpu"
    assert warning is not None


def test_select_device_uses_cpu_when_cuda_unavailable():
    import torch

    with patch.object(torch.cuda, "is_available", return_value=False):
        device, warning = pu.select_device()
    assert device == "cpu"
    assert warning is not None
    assert "Colab" in warning


def test_select_device_uses_cuda_when_available():
    import torch

    with patch.object(torch.cuda, "is_available", return_value=True):
        device, warning = pu.select_device()
    assert device == "cuda"
    assert warning is None


# ---------------------------------------------------------------------------
# Basic eval stats (pure python, no sacrebleu needed)
# ---------------------------------------------------------------------------

def test_basic_eval_stats_detect_empty_and_source_copy_and_duplicates():
    sources = ["Madigár", "Kéduh", "Sandawà"]
    predictions = ["", "Kéduh", "Close but not quite"]
    references = ["Good", "Pity", "Good"]

    stats = pu.compute_basic_eval_stats(sources, predictions, references)
    assert stats["empty_output_rate"] == pytest.approx(1 / 3)
    assert stats["source_copy_rate"] == pytest.approx(1 / 3)
    assert stats["exact_match_rate"] == 0.0
