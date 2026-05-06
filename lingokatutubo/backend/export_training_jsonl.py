"""
Export the phrasebook dataset into JSONL translation pairs for model prep.

This utility prepares data only. It does not train, fine-tune, load, or invoke
ByT5, NLLB, transformers, or any neural translation model.
"""

import argparse
import hashlib
import json
import os
import random
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from translation_dataset import SUPPORTED_LANGS, UNKNOWN_FOR_REVIEW, TranslationDataset


REQUIRED_DIRECTIONS: Tuple[Tuple[str, str], ...] = (
    ("tagabawa", "english"),
    ("english", "tagabawa"),
    ("tagabawa", "filipino"),
    ("filipino", "tagabawa"),
    ("tagabawa", "cebuano"),
    ("cebuano", "tagabawa"),
    ("english", "filipino"),
    ("filipino", "english"),
    ("english", "cebuano"),
    ("cebuano", "english"),
    ("filipino", "cebuano"),
    ("cebuano", "filipino"),
)

LANG_CODES = {
    "tagabawa": "bgs",
    "english": "eng",
    "filipino": "fil",
    "cebuano": "ceb",
}

TRAIN_RATIO = 0.80
DEV_RATIO = 0.10
TEST_RATIO = 0.10


def _text(row: Dict, language: str) -> str:
    return str(row.get(f"{language}_source", "") or row.get(language, "") or "").strip()


def _is_trainable_text(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and UNKNOWN_FOR_REVIEW not in text


def _record_base_id(row: Dict, fallback_index: int) -> str:
    raw_id = row.get("id", fallback_index)
    try:
        return f"{int(raw_id):04d}"
    except (TypeError, ValueError):
        return re.sub(r"[^A-Za-z0-9_-]+", "-", str(raw_id)).strip("-") or f"{fallback_index:04d}"


def _record_id(source_language: str, target_language: str, row: Dict, fallback_index: int) -> str:
    return (
        f"{LANG_CODES[source_language]}-{LANG_CODES[target_language]}-"
        f"{_record_base_id(row, fallback_index)}"
    )


def _normalized_pair_key(source_language: str, target_language: str, source: str, target: str) -> Tuple[str, str, str, str]:
    return (
        source_language,
        target_language,
        re.sub(r"\s+", " ", source.strip().casefold()),
        re.sub(r"\s+", " ", target.strip().casefold()),
    )


def iter_translation_pairs(
    dataset: TranslationDataset,
    directions: Sequence[Tuple[str, str]] = REQUIRED_DIRECTIONS,
    split_by_row_id: Optional[Dict[str, str]] = None,
) -> Iterable[Dict]:
    for fallback_index, row in enumerate(dataset.data, start=1):
        row_id = row.get("id", fallback_index)
        split = split_by_row_id.get(str(row_id)) if split_by_row_id else None

        for source_language, target_language in directions:
            source_text = _text(row, source_language)
            target_text = _text(row, target_language)
            if not (_is_trainable_text(source_text) and _is_trainable_text(target_text)):
                continue

            record = {
                "id": _record_id(source_language, target_language, row, fallback_index),
                "source_lang": source_language,
                "target_lang": target_language,
                "source_text": source_text,
                "target_text": target_text,
                "domain": str(row.get("source", "") or "phrasebook").strip() or "phrasebook",
                "verified_by_sme": bool(row.get("verified_by_sme", False)),
                "quality_score": row.get("quality_score"),
                "notes": str(row.get("notes", "") or "").strip(),
                "row_id": row_id,
                "topic": str(row.get("topic", "") or "").strip(),
                "metadata": {
                    "dataset_role": "phrasebook_translation_memory",
                    "source": str(row.get("source", "") or "phrasebook").strip() or "phrasebook",
                    "topic": str(row.get("topic", "") or "").strip(),
                    "notes": str(row.get("notes", "") or "").strip(),
                    "direction": f"{source_language}->{target_language}",
                },
            }
            if split:
                record["split"] = split
            yield record


class _DisjointSet:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _row_group_splits(
    dataset: TranslationDataset,
    seed: int = 42,
) -> Dict[str, str]:
    rows = list(dataset.data)
    dsu = _DisjointSet(len(rows))
    pair_owner: Dict[Tuple[str, str, str, str], int] = {}

    for index, row in enumerate(rows):
        for source_language, target_language in REQUIRED_DIRECTIONS:
            source_text = _text(row, source_language)
            target_text = _text(row, target_language)
            if not (_is_trainable_text(source_text) and _is_trainable_text(target_text)):
                continue
            pair_key = _normalized_pair_key(source_language, target_language, source_text, target_text)
            owner = pair_owner.get(pair_key)
            if owner is None:
                pair_owner[pair_key] = index
            else:
                dsu.union(owner, index)

    grouped_rows: Dict[int, List[Dict]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped_rows[dsu.find(index)].append(row)

    groups = list(grouped_rows.values())

    def stable_group_key(group: List[Dict]) -> str:
        ids = [str(row.get("id", "")) for row in group]
        text = "|".join(ids)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    rng = random.Random(seed)
    groups.sort(key=lambda group: (-len(group), rng.random(), stable_group_key(group)))

    total_rows = len(rows)
    targets = {
        "train": round(total_rows * TRAIN_RATIO),
        "dev": round(total_rows * DEV_RATIO),
    }
    targets["test"] = total_rows - targets["train"] - targets["dev"]

    split_by_row_id: Dict[str, str] = {}
    counts = {"train": 0, "dev": 0, "test": 0}
    for group in groups:
        group_size = len(group)
        split = max(
            ("train", "dev", "test"),
            key=lambda candidate: (
                targets[candidate] - counts[candidate],
                -counts[candidate],
            ),
        )

        counts[split] += group_size
        for row in group:
            split_by_row_id[str(row.get("id"))] = split

    return split_by_row_id


def analyze_dataset(dataset: TranslationDataset, records: Sequence[Dict], split_by_row_id: Dict[str, str]) -> Dict:
    rows = list(dataset.data)
    missing_by_language = {
        language: sum(1 for row in rows if not _text(row, language))
        for language in SUPPORTED_LANGS
    }
    usable_rows = sum(1 for row in rows if all(_is_trainable_text(_text(row, lang)) for lang in SUPPORTED_LANGS))

    full_row_keys = Counter(
        tuple(_text(row, lang) for lang in SUPPORTED_LANGS)
        for row in rows
    )
    duplicate_full_rows = sum(count - 1 for count in full_row_keys.values() if count > 1)

    replacement_rows = []
    control_rows = []
    tagabawa_diacritic_rows = []
    for row in rows:
        row_id = row.get("id")
        row_text = " ".join(_text(row, lang) for lang in SUPPORTED_LANGS)
        if "\ufffd" in row_text:
            replacement_rows.append(row_id)
        if any(unicodedata.category(ch)[0] == "C" and ch not in "\t\n\r" for ch in row_text):
            control_rows.append(row_id)
        tagabawa_text = _text(row, "tagabawa")
        if any(unicodedata.combining(ch) for ch in unicodedata.normalize("NFD", tagabawa_text)):
            tagabawa_diacritic_rows.append(row_id)

    direction_counts = Counter(
        f"{record['source_lang']}->{record['target_lang']}"
        for record in records
    )
    split_counts = Counter(record.get("split", "unsplit") for record in records)
    unverified_rows = sum(1 for row in rows if not bool(row.get("verified_by_sme", False)))
    notes_need_review = sum(
        1
        for row in rows
        if any(
            marker in str(row.get("notes", "") or "").casefold()
            for marker in ("need to double check", "manual check")
        )
    )
    auto_split_rows = sum(1 for row in rows if "auto-split" in str(row.get("notes", "") or "").casefold())

    leakage = find_split_leakage(records)

    return {
        "total_rows": len(rows),
        "usable_rows": usable_rows,
        "skipped_rows": len(rows) - usable_rows,
        "language_fields": list(SUPPORTED_LANGS),
        "missing_values_by_language": missing_by_language,
        "duplicate_full_row_count": duplicate_full_rows,
        "replacement_character_row_count": len(replacement_rows),
        "replacement_character_row_ids": replacement_rows,
        "control_character_row_count": len(control_rows),
        "control_character_row_ids": control_rows,
        "tagabawa_rows_with_diacritics": len(tagabawa_diacritic_rows),
        "direction_counts": dict(sorted(direction_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "split_row_counts": dict(sorted(Counter(split_by_row_id.values()).items())),
        "sme_verified_rows": len(rows) - unverified_rows,
        "unverified_rows": unverified_rows,
        "rows_marked_for_manual_or_double_check": notes_need_review,
        "auto_split_rows": auto_split_rows,
        "unknown_fallback_records": sum(
            1
            for record in records
            if UNKNOWN_FOR_REVIEW in record["source_text"] or UNKNOWN_FOR_REVIEW in record["target_text"]
        ),
        "split_leakage_duplicate_pairs": leakage,
        "nmt_readiness": "ready_for_small_experimental_fine_tuning",
    }


def find_split_leakage(records: Sequence[Dict]) -> List[Dict]:
    pair_splits: Dict[Tuple[str, str, str, str], set] = defaultdict(set)
    for record in records:
        pair_key = _normalized_pair_key(
            record["source_lang"],
            record["target_lang"],
            record["source_text"],
            record["target_text"],
        )
        pair_splits[pair_key].add(record.get("split", "unsplit"))

    leakage = []
    for pair_key, splits in pair_splits.items():
        if len(splits) > 1:
            leakage.append({
                "source_lang": pair_key[0],
                "target_lang": pair_key[1],
                "source_text": pair_key[2],
                "target_text": pair_key[3],
                "splits": sorted(splits),
            })
    return leakage


def write_jsonl(path: str, records: Iterable[Dict]) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


def export_jsonl(dataset_path: str, output_dir: str, seed: int = 42) -> Dict:
    dataset = TranslationDataset(dataset_path)
    if not dataset.is_loaded:
        raise RuntimeError(f"Dataset could not be loaded: {dataset_path}")

    split_by_row_id = _row_group_splits(dataset, seed=seed)
    records = list(iter_translation_pairs(dataset, split_by_row_id=split_by_row_id))

    output_paths = {
        "all": os.path.join(output_dir, "translation_pairs_all.jsonl"),
        "train": os.path.join(output_dir, "translation_pairs_train.jsonl"),
        "dev": os.path.join(output_dir, "translation_pairs_dev.jsonl"),
        "test": os.path.join(output_dir, "translation_pairs_test.jsonl"),
        "report": os.path.join(output_dir, "dataset_quality_report.json"),
    }

    counts = {
        "all": write_jsonl(output_paths["all"], records),
    }
    for split in ("train", "dev", "test"):
        counts[split] = write_jsonl(
            output_paths[split],
            (record for record in records if record.get("split") == split),
        )

    report = analyze_dataset(dataset, records, split_by_row_id)
    report["files"] = output_paths
    report["record_counts"] = counts
    with open(output_paths["report"], "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return report


def main() -> None:
    base_dir = os.path.dirname(__file__)
    parser = argparse.ArgumentParser(
        description="Export phrasebook translation pairs and train/dev/test JSONL files."
    )
    parser.add_argument(
        "--dataset",
        default=os.path.join(base_dir, "translation_data.json"),
        help="Path to translation_data.json, CSV, or XLSX.",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(base_dir, "model_prep"),
        help="Directory for JSONL split files and quality report.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic split seed.",
    )
    args = parser.parse_args()

    report = export_jsonl(args.dataset, args.output_dir, seed=args.seed)
    print(f"Exported {report['record_counts']['all']} JSONL translation pairs")
    print(f"Train/dev/test: {report['record_counts']['train']}/"
          f"{report['record_counts']['dev']}/{report['record_counts']['test']}")
    print(f"Quality report: {report['files']['report']}")


if __name__ == "__main__":
    main()
