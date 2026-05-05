"""
Export the phrasebook dataset into JSONL translation pairs for future model work.

This utility prepares data only. It does not train, fine-tune, load, or invoke
ByT5, NLLB, transformers, or any neural translation model.
"""

import argparse
import json
import os
from typing import Dict, Iterable

from translation_dataset import SUPPORTED_LANGS, TranslationDataset


def iter_translation_pairs(dataset: TranslationDataset) -> Iterable[Dict]:
    for row in dataset.data:
        row_id = row.get("id")
        topic = row.get("topic", "")
        source = row.get("source", "phrasebook")

        for source_language in SUPPORTED_LANGS:
            source_text = str(row.get(f"{source_language}_source", "") or "").strip()
            if not source_text:
                continue

            for target_language in SUPPORTED_LANGS:
                if source_language == target_language:
                    continue

                target_text = str(row.get(f"{target_language}_source", "") or "").strip()
                if not target_text or target_text == source_text:
                    continue

                yield {
                    "source_language": source_language,
                    "target_language": target_language,
                    "source_text": source_text,
                    "target_text": target_text,
                    "dataset_role": "phrasebook_translation_memory",
                    "row_id": row_id,
                    "topic": topic,
                    "source": source,
                }


def export_jsonl(dataset_path: str, output_path: str) -> int:
    dataset = TranslationDataset(dataset_path)
    if not dataset.is_loaded:
        raise RuntimeError(f"Dataset could not be loaded: {dataset_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    count = 0
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        for record in iter_translation_pairs(dataset):
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> None:
    base_dir = os.path.dirname(__file__)
    parser = argparse.ArgumentParser(
        description="Export phrasebook translation pairs to JSONL for later fine-tuning prep."
    )
    parser.add_argument(
        "--dataset",
        default=os.path.join(base_dir, "translation_data.json"),
        help="Path to translation_data.json, CSV, or XLSX.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(base_dir, "model_prep", "translation_pairs.jsonl"),
        help="Output JSONL path.",
    )
    args = parser.parse_args()

    count = export_jsonl(args.dataset, args.output)
    print(f"Exported {count} JSONL translation pairs to {args.output}")


if __name__ == "__main__":
    main()
