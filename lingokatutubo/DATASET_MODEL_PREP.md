# Dataset and Model Preparation

**Status:** Dataset preparation and low-resource MT experimentation only.
No neural translation model is loaded, trained, fine-tuned, or invoked by the
active backend.

## Current Dataset Reality

The active dataset is [backend/translation_data.json](backend/translation_data.json).
The current audit found:

- Total rows: **1015**
- Usable rows with Tagabawa, English, Filipino, and Cebuano filled: **1015**
- Skipped rows for empty required language text: **0**
- Full duplicate multilingual rows: **7**
- Rows with Tagabawa diacritics/accents: **661**
- Rows containing Unicode replacement characters: **0**
- Rows containing unexpected control characters: **0**
- SME-verified rows: **0**
- Unverified rows: **1015**
- Rows marked `need to double check` or `manual check`: **30**
- Rows marked as `auto-split from slash values`: **346**

The dataset is useful as a phrasebook / translation memory and as seed data
for experiments. It is not large enough, diverse enough, or sufficiently
verified to claim high-accuracy neural machine translation.

## Current Runtime Translator

Production translation should remain dataset-first:

1. Exact phrase lookup.
2. Fuzzy phrase lookup.
3. Word-by-word fallback.
4. `[UNKNOWN_FOR_REVIEW]` for unknown phrases in pipeline metadata/output.

Do not replace this path with ByT5, NLLB, or any neural model until an
evaluation set and SME review process prove that model quality is better than
the phrasebook baseline.

## JSONL Export

Use [backend/export_training_jsonl.py](backend/export_training_jsonl.py):

```powershell
cd .\backend
.\.venv\Scripts\python.exe .\export_training_jsonl.py
```

Generated files:

- `backend/model_prep/translation_pairs_all.jsonl`
- `backend/model_prep/translation_pairs_train.jsonl`
- `backend/model_prep/translation_pairs_dev.jsonl`
- `backend/model_prep/translation_pairs_test.jsonl`
- `backend/model_prep/dataset_quality_report.json`

Current export summary:

- All records: **12180**
- Train records: **9744**
- Dev records: **1224**
- Test records: **1212**
- Split row counts: train **812**, dev **102**, test **101**
- Records per required direction: **1015**
- Unknown/fallback records in export: **0**
- Duplicate source-target leakage across splits: **0**

Each JSONL record preserves the phrase row ID, direction, source/target
languages, source/target text, phrasebook domain, topic, notes, SME flag,
quality score placeholder, and metadata.

Example:

```json
{
  "id": "bgs-eng-0001",
  "source_lang": "tagabawa",
  "target_lang": "english",
  "source_text": "Madigár",
  "target_text": "Hello",
  "domain": "phrasebook",
  "verified_by_sme": false,
  "quality_score": null,
  "notes": "",
  "row_id": 2,
  "topic": "visiting",
  "metadata": {
    "dataset_role": "phrasebook_translation_memory",
    "source": "phrasebook",
    "topic": "visiting",
    "notes": "",
    "direction": "tagabawa->english"
  },
  "split": "train"
}
```

## Required Directions

The exporter writes all 12 directions:

- Tagabawa -> English
- English -> Tagabawa
- Tagabawa -> Filipino
- Filipino -> Tagabawa
- Tagabawa -> Cebuano
- Cebuano -> Tagabawa
- English -> Filipino
- Filipino -> English
- English -> Cebuano
- Cebuano -> English
- Filipino -> Cebuano
- Cebuano -> Filipino

## Split Policy

The exporter uses an 80/10/10 train/dev/test split. Rows that share duplicate
source-target pairs are grouped before splitting, so the same pair does not
appear in multiple splits. This prevents phrase-level leakage across train,
validation, and test data.

The split is deterministic by seed and preserves IDs and metadata.

## Model Recommendation

### Current App

Keep phrasebook / translation memory / fuzzy matching in production.

### NLLB

Use NLLB only as a baseline/experiment for language pairs it actually
supports, such as English, Filipino/Tagalog, and Cebuano where available.
Do not assume Bagobo-Tagabawa is supported, and do not claim NLLB solves
Bagobo-Tagabawa translation.

### ByT5-small

ByT5-small is the safest Bagobo-Tagabawa fine-tuning candidate for experiments
because byte-level models are more tolerant of diacritics and unusual
orthography. With only about one thousand phrasebook rows, expected behavior is
memorization/generalization over short phrases, not reliable document-level MT.

### Do Not Train From Scratch

Training from scratch is not appropriate for this dataset size. Future work
should adapt pretrained models only and compare them against the phrasebook
baseline.

## Evaluation Plan

Before any neural model is integrated into the app, evaluate:

- Exact phrase accuracy against the held-out test split.
- Fuzzy match score against references.
- BLEU for rough n-gram overlap.
- chrF or chrF++ for character-level similarity and diacritic sensitivity.
- Unknown/fallback rate.
- Human/SME review for cultural, dialect, and educational correctness.

SME review is required before claiming reliable Bagobo-Tagabawa translation.
Unverified outputs should remain marked as needing review.

## NMT Readiness Verdict

**Ready for small experimental fine-tuning.**

The project is ready to prepare data and run controlled ByT5-small/NLLB
baseline experiments outside the production backend. It is not ready for
production NMT integration.

## Safe Capstone Claims

- The system performs document upload, OCR, extraction, phrasebook translation,
  preview, and translated PDF download.
- The current translator uses a multilingual phrasebook / translation memory
  with exact and fuzzy matching.
- The dataset can be exported into JSONL train/dev/test files for future MT
  experiments.
- Neural MT is planned as future experimental work and requires evaluation and
  SME validation before production use.

## Claims To Avoid

- High-accuracy NMT.
- Fully intelligent translation.
- Production-ready neural translation.
- Complete Bagobo OCR.
- Perfect contextual translation.
