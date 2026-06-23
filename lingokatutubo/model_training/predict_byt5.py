"""Translate a single Bagobo-Tagabawa sentence with a fine-tuned ByT5 checkpoint.

This is an experimental research script, not the Django app's translation
path. Every result is marked needs_review=True — no confidence score is
fabricated, because the model has not been validated against quality
thresholds.

Usage (PowerShell):
    python model_training/predict_byt5.py --text "Bagobo sentence here"
    python model_training/predict_byt5.py --text "..." --model-dir model_artifacts/byt5_tagabawa_english_v1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline_utils import (  # noqa: E402
    build_prefixed_input,
    format_prediction,
    harden_cpu_threading,
    resolve_model_dir,
    select_device,
)

DEFAULT_MODEL_DIR = Path("model_artifacts/byt5_tagabawa_english_v1")
MAX_TARGET_LENGTH = 512


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True, help="Bagobo-Tagabawa sentence to translate")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    return parser.parse_args()


def run_prediction(model, tokenizer, device: str, source_text: str, model_version: str) -> dict:
    """Core prediction logic, separated from CLI/model-loading so tests can
    pass in stub model/tokenizer objects without downloading real weights.
    """
    prompt = build_prefixed_input(source_text)
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    generated_ids = model.generate(**inputs, max_length=MAX_TARGET_LENGTH)
    generated_text = tokenizer.decode(generated_ids[0], skip_special_tokens=True)
    return format_prediction(source_text, generated_text, model_version)


def main() -> None:
    args = parse_args()
    model_dir = resolve_model_dir(args.model_dir)

    harden_cpu_threading()
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    torch.set_num_threads(1)

    device, warning = select_device()
    if warning:
        print(f"[predict_byt5] WARNING: {warning}")

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSeq2SeqLM.from_pretrained(str(model_dir)).to(device)
    model.eval()

    result = run_prediction(model, tokenizer, device, args.text, model_version=str(model_dir))

    print(f"Source text:     {result['source_text']}")
    print(f"Generated (EN):  {result['generated_text']}")
    print(f"Model version:   {result['model_version']}")
    print(f"needs_review:    {result['needs_review']}")
    print(f"Note:            {result['note']}")


if __name__ == "__main__":
    main()
