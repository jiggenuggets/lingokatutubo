# Dataset and Model Preparation

**Status:** P2 planning and data preparation. No neural translation model is loaded, trained, fine-tuned, or invoked by the active backend.

## Current Dataset Reality

The active dataset is [backend/translation_data.json](backend/translation_data.json). It currently contains **1015 phrase rows**, not exactly 900.

Each row has these plain-language columns:

```json
{
  "id": 1,
  "topic": "visiting",
  "tagabawa": "Ágpanumbalé",
  "english": "Visiting",
  "filipino": "Pagdalaw",
  "cebuano": "Pagbisita",
  "source": "phrasebook",
  "notes": ""
}
```

The backend loader normalizes `english`, `tagabawa`, `filipino`, and `cebuano` into internal `*_source` keys before building lookup indices.

## Correct Use Today

Use the dataset first as a **phrasebook / translation memory**:

1. Exact phrase lookup.
2. Fuzzy phrase lookup.
3. Word-by-word fallback.
4. Return original text when no phrasebook match exists.

Do **not** describe this dataset as enough for high-accuracy neural translation. It is useful seed data, not a complete parallel corpus.

## Recommended JSONL Format Later

For model preparation, export supervised translation pairs as JSONL. One line should represent one source-target direction:

```json
{"source_language":"english","target_language":"tagabawa","source_text":"Visiting","target_text":"Ágpanumbalé","dataset_role":"phrasebook_translation_memory","row_id":1,"topic":"visiting","source":"phrasebook"}
```

The helper script [backend/export_training_jsonl.py](backend/export_training_jsonl.py) prepares this format:

```powershell
cd .\backend
python .\export_training_jsonl.py
```

Default output:

```text
backend/model_prep/translation_pairs.jsonl
```

This export step is data preparation only. It does not train or invoke any model.

## Planned Model Strategy

- **ByT5-small** is planned for Bagobo-Tagabawa directions because byte-level modeling is a better fit for low-resource orthography and accented forms.
- **NLLB-200 distilled 600M** is planned for English-Cebuano-Tagalog directions because those languages are better represented in multilingual pretrained translation models.
- Do **not** train from scratch. Future work should fine-tune or adapt pretrained models only after the structured JSON contract, OCR path, and review workflow are stable.

## Runtime Status

The active runtime is still dataset-first. There is no active `transformers`, `AutoModel`, `from_pretrained`, ByT5, or NLLB code path in the backend translation pipeline.
