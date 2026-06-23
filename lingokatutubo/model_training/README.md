# ByT5 Bagobo-Tagabawa → English — experimental training pipeline

This is phase one of the first neural MT experiment for LingoKatutubo. It is
**not** wired into the Django app. It only covers dataset preparation,
fine-tuning `google/byt5-small`, prediction, and evaluation.

Translation direction: **Bagobo-Tagabawa → English only.**

Status: experimental. All model output requires expert review before any
use. BLEU/chrF scores measure surface overlap with reference text, not
cultural or linguistic correctness.

## Dataset

Source: `datasets/cleaned/cross_lingual_cleaned_v1.csv` (~612 raw rows; a
handful are dropped during cleaning — see `datasets/byt5_v1/split_manifest.json`
for the exact counts and reasons). Only `bagobo_text` (source) and
`english_text` (target) are used. The existing ~1,028-entry word/phrase
dataset (`translator/services/translation_data.json`) is intentionally
**not** included here — it stays reserved for phrasebook/glossary lookup.

### Split

The CSV has no `source_document` / `story_id` / `chapter` / `paragraph_group`
column, so there is no way to split by narrative without guessing. The split
is therefore a deterministic **`experimental_row_split`** (seed 42, 80/10/10),
not a leakage-safe split by document. **High-priority limitation:** sentences
from the same oral narrative can land in both train and test. A source/story-
tagged split must be built before this is used for final thesis evaluation.

## 1. Set up a separate environment

Do not install these into the Django app's `.venv`.

```powershell
py -3.11 -m venv .venv-training
.\.venv-training\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-training.txt
```

## 2. Prepare the dataset

```powershell
python model_training/prepare_dataset.py
```

Writes `datasets/byt5_v1/{train,validation,test}.{csv,jsonl}` and
`split_manifest.json`. Never modifies the source CSV.

## 3. Smoke test (fast, proves the pipeline runs)

```powershell
python model_training/train_byt5.py --max-train-samples 20 --epochs 1
```

## 4. Full training run

```powershell
python model_training/train_byt5.py
```

Saves the best checkpoint to `model_artifacts/byt5_tagabawa_english_v1/`
(gitignored — model weights are never committed) along with
`training_manifest.json` recording exact parameters, package/CUDA versions,
and timestamps.

## 5. Evaluate (test split only)

```powershell
python model_training/evaluate_byt5.py
```

Writes `model_artifacts/byt5_tagabawa_english_v1/evaluation.json` and
`predictions.csv`.

## 6. Translate one sentence

```powershell
python model_training/predict_byt5.py --text "Madigár su kannun kó."
```

Always prints `needs_review=True`. No confidence score is invented.

## Known warnings (harmless)

- **`shared.weight and lm_head.weight are both present with different
  values`** — `google/byt5-small`'s published checkpoint is a T5-family
  model with `tie_word_embeddings=True` (confirmed via
  `AutoConfig.from_pretrained("google/byt5-small")`). T5/ByT5/mT5 checkpoints
  on the Hub store both tensors, but `transformers` re-ties them
  automatically right after loading, so the saved `lm_head.weight` copy is
  never actually used. This is long-documented upstream behavior, not a sign
  of a bad download. We do **not** set `tie_word_embeddings=False` or
  otherwise touch the weights — doing so would be a speculative change to a
  model that is already configured correctly by its publisher.
- **`'pin_memory' argument is set as true but no accelerator is found`** —
  expected on a CPU-only run; harmless.
- **OpenMP/MKL CPU segfault** — on this repo's CPU-only torch build, running
  the default multi-threaded CPU kernels for tokenization + a forward/
  backward pass back to back reproducibly segfaults. `pipeline_utils.harden_cpu_threading()`
  sets `OMP_NUM_THREADS=1` / `MKL_NUM_THREADS=1` (must run before `import
  torch`) and `train_byt5.py`/`evaluate_byt5.py`/`predict_byt5.py` also call
  `torch.set_num_threads(1)` as a second layer of safety. This trades some
  CPU speed for stability; it does not affect a CUDA run's GPU throughput.

## Hardware

If no CUDA GPU is detected, the scripts warn and continue on CPU rather than
failing — full training will be slow on CPU; use `--max-train-samples 20
--epochs 1` for a smoke test, or run the full job on Google Colab:

1. Open a new Colab notebook, set Runtime → Change runtime type → GPU.
2. Upload/clone this repository (at least `model_training/`, `datasets/`,
   `requirements-training.txt`).
3. `!pip install -r requirements-training.txt`
4. `!python model_training/prepare_dataset.py`
5. `!python model_training/train_byt5.py`

## Tests

Lightweight tests (no model download, no GPU) live in `model_training/tests/`
and require `pytest` (included in `requirements-training.txt`; install
separately with `python -m pip install pytest` if needed). Run with the
training virtualenv active:

```powershell
python -m pytest model_training\tests -v
```

Do not use `python -m unittest discover` here — the test file uses pytest
fixtures (`tmp_path`) and `pytest.raises`/`pytest.approx`, so it requires the
pytest runner, not `unittest`.

These are intentionally **not** part of `python manage.py test` — they
depend on `pandas`/`scikit-learn`, which are not installed in the Django
app's environment.
