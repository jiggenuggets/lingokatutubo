"""Fine-tune google/byt5-small for Bagobo-Tagabawa -> English translation.

Experimental phase-one pipeline. Not wired into the Django app.

Usage (PowerShell):
    # Smoke test (fast, proves the pipeline runs end to end):
    python model_training/train_byt5.py --max-train-samples 20 --epochs 1

    # Full experiment:
    python model_training/train_byt5.py

If no CUDA GPU is available, this prints a warning and a Colab-compatible
path rather than failing.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline_utils import TASK_PREFIX, build_prefixed_input, harden_cpu_threading, select_device  # noqa: E402

MODEL_NAME = "google/byt5-small"
DEFAULT_DATA_DIR = Path("datasets/byt5_v1")
DEFAULT_MODEL_DIR = Path("model_artifacts/byt5_tagabawa_english_v1")
DEFAULT_CHECKPOINT_DIR = Path("training_outputs/byt5_tagabawa_english_v1")
SEED = 42
MAX_SOURCE_LENGTH = 512
MAX_TARGET_LENGTH = 512


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--train-batch-size", type=int, default=2)
    parser.add_argument("--eval-batch-size", type=int, default=2)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=4)
    parser.add_argument("--early-stopping-patience", type=int, default=3)
    parser.add_argument(
        "--max-train-samples",
        type=int,
        default=None,
        help="Truncate the training split for a fast smoke test, e.g. 20.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started_at = datetime.now(timezone.utc).isoformat()

    # Must run before `import torch` to take effect (see docstring) — this
    # repo's CPU torch build segfaults under default multi-threaded OpenMP
    # when running tokenizers + a forward/backward pass back to back.
    harden_cpu_threading()

    # Heavy imports happen here (not at module level) so other tooling can
    # import this module's argument parsing without requiring torch.
    import torch
    from datasets import load_dataset
    from transformers import (
        AutoModelForSeq2SeqLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        EarlyStoppingCallback,
        Seq2SeqTrainer,
        Seq2SeqTrainingArguments,
        set_seed,
    )

    torch.set_num_threads(1)

    set_seed(SEED)

    device, warning = select_device()
    if warning:
        print(f"[train_byt5] WARNING: {warning}")
    print(f"[train_byt5] device: {device}")

    train_path = args.data_dir / "train.jsonl"
    val_path = args.data_dir / "validation.jsonl"
    for p in (train_path, val_path):
        if not p.exists():
            raise FileNotFoundError(
                f"{p} not found. Run `python model_training/prepare_dataset.py` first."
            )

    data_files = {"train": str(train_path), "validation": str(val_path)}
    raw_datasets = load_dataset("json", data_files=data_files)

    if args.max_train_samples is not None:
        n = min(args.max_train_samples, len(raw_datasets["train"]))
        raw_datasets["train"] = raw_datasets["train"].select(range(n))
        print(f"[train_byt5] smoke test: truncated training split to {n} examples")

        # predict_with_generate eval over the full validation split is far
        # too slow for a CPU smoke test (autoregressive generation per
        # example); cap it too so the smoke test stays fast. The full run
        # (no --max-train-samples) always evaluates on the complete split.
        n_val = min(n, len(raw_datasets["validation"]))
        raw_datasets["validation"] = raw_datasets["validation"].select(range(n_val))
        print(f"[train_byt5] smoke test: truncated validation split to {n_val} examples")

    print(f"[train_byt5] loading {MODEL_NAME}")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME)

    def preprocess(batch):
        inputs = [build_prefixed_input(t) for t in batch["source_text"]]
        model_inputs = tokenizer(inputs, max_length=MAX_SOURCE_LENGTH, truncation=True)
        labels = tokenizer(text_target=batch["target_text"], max_length=MAX_TARGET_LENGTH, truncation=True)
        model_inputs["labels"] = labels["input_ids"]
        return model_inputs

    tokenized = raw_datasets.map(preprocess, batched=True, remove_columns=raw_datasets["train"].column_names)

    data_collator = DataCollatorForSeq2Seq(tokenizer=tokenizer, model=model)

    # Mixed precision only makes sense (and is only reliably supported) on CUDA.
    # bf16 and dataloader workers are also pinned safe-for-CPU explicitly
    # (rather than relying on library defaults) so the intent is visible.
    use_fp16 = device == "cuda"
    use_bf16 = False
    dataloader_num_workers = 0

    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    args.model_dir.mkdir(parents=True, exist_ok=True)

    # ByT5 is byte-level, so 512 generated bytes per eval example on CPU is
    # slow; only shorten this for the smoke test, never for the full run.
    generation_max_length = 64 if args.max_train_samples is not None else MAX_TARGET_LENGTH

    training_args = Seq2SeqTrainingArguments(
        output_dir=str(args.checkpoint_dir),
        seed=SEED,
        learning_rate=args.learning_rate,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.train_batch_size,
        per_device_eval_batch_size=args.eval_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        save_total_limit=2,
        predict_with_generate=True,
        generation_max_length=generation_max_length,
        fp16=use_fp16,
        bf16=use_bf16,
        dataloader_num_workers=dataloader_num_workers,
        report_to=[],
        logging_steps=10,
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["validation"],
        data_collator=data_collator,
        # transformers >=5.0 removed the `tokenizer` kwarg from
        # Seq2SeqTrainer.__init__ in favor of `processing_class` (it was
        # already the preferred name, with `tokenizer` deprecated, as far
        # back as 4.41 — so this is safe across the whole pinned range).
        processing_class=tokenizer,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=args.early_stopping_patience)],
    )

    print("[train_byt5] starting training")
    train_result = trainer.train()
    print(f"[train_byt5] training complete: {train_result.metrics}")

    print(f"[train_byt5] saving best model to {args.model_dir}")
    trainer.save_model(str(args.model_dir))
    tokenizer.save_pretrained(str(args.model_dir))

    finished_at = datetime.now(timezone.utc).isoformat()

    def _pkg_version(pkg: str) -> str:
        try:
            module = __import__(pkg)
            return getattr(module, "__version__", "unknown")
        except ImportError:
            return "not installed"

    manifest = {
        "model_name": MODEL_NAME,
        "task_prefix": TASK_PREFIX,
        "translation_direction": "Bagobo-Tagabawa -> English",
        "python_version": platform.python_version(),
        "torch_version": _pkg_version("torch"),
        "transformers_version": _pkg_version("transformers"),
        "datasets_version": _pkg_version("datasets"),
        "cuda_available": torch.cuda.is_available(),
        "device_used": device,
        "device_warning": warning,
        "training_parameters": {
            "seed": SEED,
            "learning_rate": args.learning_rate,
            "epochs": args.epochs,
            "train_batch_size": args.train_batch_size,
            "eval_batch_size": args.eval_batch_size,
            "gradient_accumulation_steps": args.gradient_accumulation_steps,
            "max_source_length": MAX_SOURCE_LENGTH,
            "max_target_length": MAX_TARGET_LENGTH,
            "early_stopping_patience": args.early_stopping_patience,
            "max_train_samples_override": args.max_train_samples,
        },
        "split_method": "experimental_row_split (see datasets/byt5_v1/split_manifest.json)",
        "dataset_record_counts": {
            "train": len(tokenized["train"]),
            "validation": len(tokenized["validation"]),
        },
        "train_metrics": train_result.metrics,
        "started_at_utc": started_at,
        "completed_at_utc": finished_at,
        "model_output_dir": str(args.model_dir),
        "checkpoint_dir": str(args.checkpoint_dir),
        "status": "smoke_test" if args.max_train_samples is not None else "full_run",
        "experimental": True,
        "review_required": True,
    }
    manifest_path = args.model_dir / "training_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[train_byt5] wrote {manifest_path}")


if __name__ == "__main__":
    main()
