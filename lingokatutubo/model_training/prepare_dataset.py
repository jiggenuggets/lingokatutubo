"""Prepare the ByT5 Bagobo-Tagabawa -> English training data.

Reads the cleaned sentence-level CSV, validates it, removes only confirmed
exact duplicates / untranslated copy-through rows, splits it, and exports
CSV + JSONL + a split manifest to datasets/byt5_v1/.

This script never modifies the source CSV.

Usage (PowerShell):
    python model_training/prepare_dataset.py
    python model_training/prepare_dataset.py --input datasets/cleaned/cross_lingual_cleaned_v1.csv --output datasets/byt5_v1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline_utils import (  # noqa: E402
    clean_dataset,
    load_dataset_csv,
    split_experimental_row_level,
    to_model_records,
    write_jsonl,
)

DEFAULT_INPUT = Path("datasets/cleaned/cross_lingual_cleaned_v1.csv")
DEFAULT_OUTPUT = Path("datasets/byt5_v1")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-frac", type=float, default=0.8)
    parser.add_argument("--val-frac", type=float, default=0.1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started_at = datetime.now(timezone.utc).isoformat()

    print(f"[prepare_dataset] loading {args.input}")
    raw_df = load_dataset_csv(args.input)
    print(f"[prepare_dataset] loaded {len(raw_df)} raw rows")

    cleaned_df, report = clean_dataset(raw_df)
    print(f"[prepare_dataset] usable records after cleaning: {report.usable_record_count}")
    if report.duplicate_record_ids:
        print(f"[prepare_dataset] WARNING duplicate record_id values: {report.duplicate_record_ids}")
    if report.exact_duplicate_pair_rows:
        print(
            "[prepare_dataset] WARNING exact (bagobo,english) duplicate pairs found "
            f"in rows {report.exact_duplicate_pair_rows}; kept first occurrence only."
        )
    if report.copy_through_rows:
        print(
            "[prepare_dataset] WARNING untranslated copy-through rows excluded "
            f"(source text repeated verbatim as the English target): {report.copy_through_rows}"
        )
    if report.replacement_char_rows:
        print(
            "[prepare_dataset] WARNING rows containing literal replacement "
            f"characters (U+FFFD) — verify source encoding: {report.replacement_char_rows}"
        )
    if report.grouping_column is None:
        print(
            "[prepare_dataset] NOTE: no source/story/chapter/paragraph grouping "
            "column found. Using experimental_row_split (see split_manifest.json)."
        )

    split = split_experimental_row_level(
        cleaned_df, seed=args.seed, train_frac=args.train_frac, val_frac=args.val_frac
    )
    if not split.manifest["record_id_overlap_check"]:
        raise RuntimeError("Internal error: record_id overlap detected across splits")

    args.output.mkdir(parents=True, exist_ok=True)

    splits = {"train": split.train, "validation": split.validation, "test": split.test}
    for name, df in splits.items():
        csv_path = args.output / f"{name}.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8")
        jsonl_path = args.output / f"{name}.jsonl"
        write_jsonl(to_model_records(df), jsonl_path)
        print(f"[prepare_dataset] wrote {csv_path} and {jsonl_path} ({len(df)} rows)")

    manifest = {
        "generated_at_utc": started_at,
        "input_csv": str(args.input),
        "input_raw_row_count": len(raw_df),
        "validation_report": report.to_dict(),
        **split.manifest,
        "schema": {"record_id": "str", "source_text": "str (bagobo_text)", "target_text": "str (english_text)"},
        "excluded_dataset_note": (
            "The existing ~1028-entry word/phrase dataset (translator/services/"
            "translation_data.json) is intentionally excluded from this neural "
            "training experiment and remains reserved for phrasebook/glossary lookup."
        ),
    }
    manifest_path = args.output / "split_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[prepare_dataset] wrote {manifest_path}")

    if not split.manifest["leakage_safe"]:
        print("\n" + "!" * 78)
        print("HIGH-PRIORITY WARNING: " + split.manifest["warning"])
        print("!" * 78 + "\n")


if __name__ == "__main__":
    main()
