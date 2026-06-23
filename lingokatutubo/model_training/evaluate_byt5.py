"""Evaluate a fine-tuned ByT5 Bagobo-Tagabawa -> English checkpoint.

Runs only against the untouched test split. Reports SacreBLEU, chrF, and a
handful of sanity-check statistics (exact match, empty output, source-copy,
generated length, duplicate predictions).

IMPORTANT: BLEU and chrF measure surface-level overlap with the reference
translation. They do NOT prove cultural or linguistic correctness for
Bagobo-Tagabawa. Expert review by a fluent speaker remains required before
any of this model's output is trusted or published.

Usage (PowerShell):
    python model_training/evaluate_byt5.py
    python model_training/evaluate_byt5.py --model-dir model_artifacts/byt5_tagabawa_english_v1
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline_utils import (  # noqa: E402
    build_prefixed_input,
    compute_basic_eval_stats,
    harden_cpu_threading,
    resolve_model_dir,
    select_device,
)

DEFAULT_DATA_DIR = Path("datasets/byt5_v1")
DEFAULT_MODEL_DIR = Path("model_artifacts/byt5_tagabawa_english_v1")
MAX_TARGET_LENGTH = 512


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--batch-size", type=int, default=4)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    model_dir = resolve_model_dir(args.model_dir)

    test_path = args.data_dir / "test.jsonl"
    if not test_path.exists():
        raise FileNotFoundError(f"{test_path} not found. Run model_training/prepare_dataset.py first.")

    records = [json.loads(line) for line in test_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"[evaluate_byt5] loaded {len(records)} untouched test records from {test_path}")

    harden_cpu_threading()
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    torch.set_num_threads(1)

    device, warning = select_device()
    if warning:
        print(f"[evaluate_byt5] WARNING: {warning}")

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSeq2SeqLM.from_pretrained(str(model_dir)).to(device)
    model.eval()

    sources = [r["source_text"] for r in records]
    references = [r["target_text"] for r in records]
    predictions: list[str] = []

    batch_size = args.batch_size
    with torch.no_grad():
        for i in range(0, len(sources), batch_size):
            batch = [build_prefixed_input(s) for s in sources[i : i + batch_size]]
            inputs = tokenizer(batch, return_tensors="pt", padding=True, truncation=True, max_length=512).to(device)
            generated = model.generate(**inputs, max_length=MAX_TARGET_LENGTH)
            decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
            predictions.extend(decoded)
            print(f"[evaluate_byt5] generated {min(i + batch_size, len(sources))}/{len(sources)}")

    import sacrebleu

    bleu = sacrebleu.corpus_bleu(predictions, [references])
    chrf = sacrebleu.corpus_chrf(predictions, [references])

    basic_stats = compute_basic_eval_stats(sources, predictions, references)

    per_sentence_chrf = []
    for pred, ref in zip(predictions, references):
        try:
            per_sentence_chrf.append(sacrebleu.sentence_chrf(pred, [ref]).score)
        except Exception:
            per_sentence_chrf.append(None)

    evaluation = {
        "model_dir": str(model_dir),
        "test_record_count": len(records),
        "sacrebleu": bleu.score,
        "sacrebleu_signature": str(bleu),
        "chrf": chrf.score,
        **basic_stats,
        "disclaimer": (
            "SacreBLEU/chrF reflect surface overlap with the reference English "
            "translations only. They do not prove cultural or linguistic "
            "correctness for Bagobo-Tagabawa. Expert review by a fluent "
            "speaker is required before this output is trusted or published."
        ),
    }

    eval_path = model_dir / "evaluation.json"
    eval_path.write_text(json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[evaluate_byt5] wrote {eval_path}")

    predictions_path = model_dir / "predictions.csv"
    with predictions_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["record_id", "bagobo_source", "reference_english", "predicted_english", "exact_match", "chrf", "review_status"]
        )
        for rec, pred, chrf_score in zip(records, predictions, per_sentence_chrf):
            exact_match = pred.strip() == rec["target_text"].strip()
            writer.writerow(
                [
                    rec["record_id"],
                    rec["source_text"],
                    rec["target_text"],
                    pred,
                    exact_match,
                    "" if chrf_score is None else round(chrf_score, 2),
                    "needs_review",
                ]
            )
    print(f"[evaluate_byt5] wrote {predictions_path}")

    print("\n=== Evaluation summary (TEST SPLIT — see split_manifest.json for leakage caveats) ===")
    print(f"SacreBLEU: {bleu.score:.2f}")
    print(f"chrF:      {chrf.score:.2f}")
    for k, v in basic_stats.items():
        print(f"{k}: {v:.4f}")
    print(
        "\nReminder: these metrics do not establish cultural or linguistic "
        "correctness. Expert review by a fluent Bagobo-Tagabawa speaker is required."
    )


if __name__ == "__main__":
    main()
