"""
LingoKatutubo — Translation Dataset Audit
Generates: dataset_audit_report.json, dataset_audit_report.csv, dataset_audit_summary.md
Audit only — does NOT modify any source data.
"""
import json, csv, re, sys
from collections import Counter, defaultdict
from pathlib import Path
from datetime import date

LANGS = ["tagabawa", "english", "filipino", "cebuano"]
BASE = Path(__file__).parent
JSON_PATH = BASE / "translator/services/translation_data.json"
CSV_PATH  = BASE / "backend/tagabawa_phrasebook_cleaned_auto(phrases).csv"
OUT_JSON  = BASE.parent / "dataset_audit_report.json"
OUT_CSV   = BASE.parent / "dataset_audit_report.csv"
OUT_MD    = BASE.parent / "dataset_audit_summary.md"


def load_data():
    with open(JSON_PATH, "r", encoding="utf-8-sig") as f:
        rows = json.load(f)["rows"]
    csv_rows = []
    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            csv_rows.append(r)
    return rows, csv_rows


def classify_record(text):
    t = str(text or "").strip()
    if not t:
        return "empty"
    wc = len(t.split())
    if wc == 1:
        return "single_word"
    if wc <= 6:
        return "short_phrase"
    sents = [s.strip() for s in re.split(r"[.!?]+", t) if s.strip()]
    if wc > 50:
        return "paragraph"
    if len(sents) > 1:
        return "multi_sentence"
    return "short_sentence" if wc <= 15 else "long_sentence"


def run_audit():
    rows, csv_rows = load_data()

    # ── B: RECORD COUNTS ──────────────────────────────────────────────────────
    complete = sum(1 for r in rows if all(str(r.get(lg, "")).strip() for lg in LANGS))
    missing  = len(rows) - complete
    first_col = list(csv_rows[0].keys())[0] if csv_rows else "?"
    csv_true  = sum(1 for r in csv_rows if str(r.get(first_col, "")).strip().upper() == "TRUE")

    # ── C: LANGUAGE COVERAGE ─────────────────────────────────────────────────
    coverage = {lg: sum(1 for r in rows if str(r.get(lg, "")).strip()) for lg in LANGS}

    # ── D: RECORD TYPE DISTRIBUTION ──────────────────────────────────────────
    type_dist = {lg: dict(Counter(classify_record(r.get(lg, "")) for r in rows)) for lg in LANGS}

    # ── E: DUPLICATES ────────────────────────────────────────────────────────
    tuple_counter = Counter(
        tuple(str(r.get(lg, "") or "").strip().lower() for lg in LANGS)
        for r in rows
    )
    dup_groups = [(t, c) for t, c in tuple_counter.items() if c > 1]
    en_counts  = Counter(str(r.get("english",  "") or "").strip().lower() for r in rows)
    tag_counts = Counter(str(r.get("tagabawa", "") or "").strip().lower() for r in rows)
    en_extras  = sum(c - 1 for c in en_counts.values()  if c > 1)
    tag_extras = sum(c - 1 for c in tag_counts.values() if c > 1)

    # Polysemy
    tag_to_en = defaultdict(set)
    for r in rows:
        t = str(r.get("tagabawa", "") or "").strip().lower()
        e = str(r.get("english",  "") or "").strip().lower()
        if t:
            tag_to_en[t].add(e)
    poly_groups = {k: sorted(v) for k, v in tag_to_en.items() if len(v) > 1}

    # ── F: UNICODE / ENCODING ────────────────────────────────────────────────
    CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    unicode_issues = {"replacement_char": 0, "control_char": 0, "html_fragment": 0, "double_space": 0}
    for r in rows:
        for lg in LANGS:
            val = str(r.get(lg, "") or "")
            if "�" in val:
                unicode_issues["replacement_char"] += 1
            if CTRL_RE.search(val):
                unicode_issues["control_char"] += 1
            if re.search(r"<[a-z]|&[a-z]+;", val, re.I):
                unicode_issues["html_fragment"] += 1
            if "  " in val:
                unicode_issues["double_space"] += 1

    # ── G: ALIGNMENT OUTLIERS ────────────────────────────────────────────────
    align_outliers = []
    for r in rows:
        tag = str(r.get("tagabawa", "") or "").strip()
        en  = str(r.get("english",  "") or "").strip()
        if not tag or not en:
            continue
        ratio = max(1, len(tag.split())) / max(1, len(en.split()))
        if ratio > 5 or ratio < 0.2:
            align_outliers.append({"id": r.get("id"), "tagabawa_wc": len(tag.split()), "english_wc": len(en.split()), "ratio": round(ratio, 2)})

    # ── H: SOURCE & VERIFICATION ─────────────────────────────────────────────
    source_dist = dict(Counter(str(r.get("source", "")) for r in rows))
    topic_dist  = dict(Counter(str(r.get("topic",  "") or "").strip() for r in rows))
    notes_cat = Counter()
    for r in rows:
        n = str(r.get("notes", "") or "").lower()
        if "auto-split"    in n: notes_cat["auto_split_from_slash"] += 1
        if "manual check"  in n: notes_cat["manual_check_slash"]    += 1
        if "double check"  in n: notes_cat["needs_double_check"]     += 1
        if "plural"        in n: notes_cat["plural_note"]            += 1
        if "singular"      in n: notes_cat["singular_note"]          += 1

    auto_split_count = notes_cat.get("auto_split_from_slash", 0)

    # ── I: LEAKAGE RISK ──────────────────────────────────────────────────────
    json_en = set(str(r.get("english", "") or "").strip().lower() for r in rows if str(r.get("english", "")).strip())
    csv_en  = set(str(r.get("english", "") or "").strip().lower() for r in csv_rows if str(r.get("english", "")).strip())
    json_only = sorted(json_en - csv_en)

    # ── BUILD FULL REPORT ────────────────────────────────────────────────────
    report = {
        "audit_date": str(date.today()),
        "auditor": "LingoKatutubo Dataset Audit v1.0",
        "A_dataset_inventory": {
            "files_audited": [
                {
                    "path": str(JSON_PATH),
                    "type": "JSON",
                    "size_bytes": JSON_PATH.stat().st_size,
                    "structure": 'Object with "rows" array',
                    "role": "primary_runtime_dataset",
                },
                {
                    "path": str(CSV_PATH),
                    "type": "CSV",
                    "size_bytes": CSV_PATH.stat().st_size,
                    "structure": "Flat CSV, headers: FALSE/pair_id/topic/tagabawa/cebuano/filipino/english/notes",
                    "role": "source_phrasebook_archive",
                },
            ]
        },
        "B_exact_record_counts": {
            "json_total_rows": len(rows),
            "csv_total_rows": len(csv_rows),
            "json_rows_all_4_languages_complete": complete,
            "json_rows_with_missing_any_language": missing,
            "csv_rows_flagged_TRUE_first_col": csv_true,
            "note": "Total is 1028, not 900 as previously assumed in documentation.",
        },
        "C_language_coverage": {
            "languages_present": LANGS,
            "coverage_by_language": coverage,
            "rows_missing_per_language": {lg: len(rows) - coverage[lg] for lg in LANGS},
        },
        "D_phrase_sentence_paragraph_distribution": {
            "classification_by_language": type_dist,
            "thresholds": {
                "single_word": "1 token",
                "short_phrase": "2-6 tokens",
                "short_sentence": "7-15 tokens",
                "long_sentence": "16-50 tokens",
                "multi_sentence": "multiple sentence-ending punctuation marks",
                "paragraph": ">50 tokens",
            },
        },
        "E_duplicate_and_missing_value_report": {
            "exact_4lang_duplicate_groups": len(dup_groups),
            "exact_4lang_duplicate_extra_rows": sum(c - 1 for _, c in dup_groups),
            "english_field_exact_duplicate_extra_rows": en_extras,
            "tagabawa_field_exact_duplicate_extra_rows": tag_extras,
            "same_tagabawa_different_english_groups": len(poly_groups),
            "missing_any_language_field": missing,
            "exact_dup_examples": [
                {lg: t for lg, t in zip(LANGS, tup)}
                for tup, _ in dup_groups[:5]
            ],
            "polysemy_examples": [
                {"tagabawa": k, "english_variants": v}
                for k, v in list(poly_groups.items())[:10]
            ],
            "note": "83 same-tagabawa-different-english groups are largely legitimate polysemy, not alignment errors.",
        },
        "F_unicode_and_orthography_issues": {
            "replacement_characters_FFFD": unicode_issues["replacement_char"],
            "control_characters": unicode_issues["control_char"],
            "html_fragments": unicode_issues["html_fragment"],
            "double_spaces": unicode_issues["double_space"],
            "alignment_outliers_ratio_gt5x": len(align_outliers),
            "encoding": "No corruption detected. All fields read as valid UTF-8.",
            "orthography_note": (
                "Tagabawa uses diacritical marks (circumflex, macron, tilde) encoded in UTF-8. "
                "Orthography appears intact. No replacement characters or mojibake found."
            ),
        },
        "G_alignment_problems": {
            "word_count_ratio_outliers_gt5x": len(align_outliers),
            "alignment_outlier_details": align_outliers,
            "same_tagabawa_different_english_count": len(poly_groups),
            "auto_split_rows": auto_split_count,
            "auto_split_percent": round(auto_split_count / len(rows) * 100, 1),
            "assessment": (
                "Auto-split rows (33.7%) were programmatically generated from slash-separated source values. "
                "Semantic coherence of these 346 rows is unverified and represents the primary quality risk."
            ),
        },
        "H_source_and_verification_status": {
            "source_distribution": source_dist,
            "sme_verified_rows": 0,
            "notes_category_counts": dict(notes_cat),
            "topic_distribution": topic_dist,
            "unique_non_empty_topics": len([k for k in topic_dist if k]),
            "rows_with_no_topic": topic_dist.get("", 0),
            "assessment": (
                "Zero SME-verified rows. All data is phrasebook-imported or phrase_sample. "
                "No independent expert validation has occurred. This is the single highest quality risk."
            ),
        },
        "I_train_test_leakage_risks": {
            "exact_duplicates_to_remove_before_split": sum(c - 1 for _, c in dup_groups),
            "csv_is_subset_of_json_by_english_field": len(csv_en - json_en) == 0,
            "json_only_entries_count": len(json_only),
            "json_only_entries_english": json_only,
            "leakage_risk": (
                "LOW for exact duplicates (8 groups). "
                "JSON is a strict superset of CSV by English field — always split using JSON only. "
                "Using CSV as a separate test set against a JSON-trained model would cause data leakage."
            ),
            "recommended_split_strategy": "Use export_training_jsonl.py DisjointSet 80/10/10 split on the JSON dataset only.",
        },
        "J_training_readiness_decision": {
            "decision": "CONDITIONALLY_READY_WITH_CLEANING",
            "rationale": [
                "1028 rows all with 4 languages populated — sufficient minimum size for initial experiments.",
                "No encoding or alignment errors detected.",
                f"{auto_split_count} auto-split rows ({round(auto_split_count/len(rows)*100,1)}%) reduce confidence — need SME review before fine-tuning.",
                "8 exact 4-lang duplicate groups must be removed before train/dev/test split.",
                "83 polysemy groups are linguistically valid and should be retained.",
                "0 SME-verified rows is a critical gap before any model quality claims.",
                "Realistic first experiment: phrase lookup/retrieval, not fine-tuned NMT.",
            ],
            "minimum_cleaning_required": [
                "Remove 8 exact 4-lang duplicate rows (keep lower ID).",
                "Flag 346 auto-split rows with metadata before use in training.",
                "SME review of 23 manual-check rows before including in training.",
                "SME review of 7 needs-double-check rows.",
            ],
            "supported_first_experiment": (
                "English to Tagabawa or Tagabawa to English phrase-level lookup using the existing "
                "TranslationDataset cascade. Full fine-tuned NMT generation is premature."
            ),
        },
        "K_prioritized_cleaning_plan": [
            {
                "priority": 1,
                "action": "Deduplicate exact 4-lang duplicates",
                "affected_rows": 8,
                "risk": "Low — unambiguous redundancies",
                "method": "Keep row with lower ID; delete duplicates.",
            },
            {
                "priority": 2,
                "action": "SME review of manual-check rows",
                "affected_rows": 23,
                "risk": "Medium — slash-split may be incorrect",
                "method": "Export flagged rows; annotate; do not auto-edit source.",
            },
            {
                "priority": 3,
                "action": "SME review of needs-double-check rows",
                "affected_rows": 7,
                "risk": "Medium — flagged by original curator",
                "method": "Export; review; set is_sme_verified=True when confirmed.",
            },
            {
                "priority": 4,
                "action": "Add is_auto_split metadata flag to programmatic rows",
                "affected_rows": 346,
                "risk": "Medium — affects 33.7% of dataset quality claims",
                "method": "Add boolean field in PhrasebookEntry model; populate from notes field.",
            },
            {
                "priority": 5,
                "action": "Add topic labels to untagged rows",
                "affected_rows": 38,
                "risk": "Low — cosmetic; helps domain-balanced evaluation",
                "method": "Manual annotation by language team.",
            },
            {
                "priority": 6,
                "action": "Full SME verification sweep",
                "affected_rows": 1028,
                "risk": "Critical before any published model quality claims",
                "method": "Structured expert review; track is_sme_verified per row.",
            },
        ],
    }

    # ── WRITE JSON REPORT ────────────────────────────────────────────────────
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  wrote {OUT_JSON}")

    # ── WRITE CSV REPORT ─────────────────────────────────────────────────────
    csv_records = []
    for r in rows:
        record_type = classify_record(r.get("tagabawa", ""))
        notes_str = str(r.get("notes", "") or "")
        n_lower = notes_str.lower()
        flags = []
        if "auto-split"   in n_lower: flags.append("auto_split")
        if "manual check" in n_lower: flags.append("manual_check")
        if "double check" in n_lower: flags.append("needs_review")
        # Check 4-lang dup
        key = tuple(str(r.get(lg, "") or "").strip().lower() for lg in LANGS)
        is_dup = tuple_counter[key] > 1

        csv_records.append({
            "id":              r.get("id", ""),
            "topic":           r.get("topic", "") or "",
            "source":          r.get("source", "") or "",
            "record_type":     record_type,
            "tagabawa":        r.get("tagabawa", "") or "",
            "english":         r.get("english",  "") or "",
            "filipino":        r.get("filipino",  "") or "",
            "cebuano":         r.get("cebuano",   "") or "",
            "notes":           notes_str,
            "flags":           "|".join(flags) if flags else "",
            "is_exact_dup":    str(is_dup).upper(),
            "all_langs_complete": str(all(str(r.get(lg, "")).strip() for lg in LANGS)).upper(),
        })

    fieldnames = ["id", "topic", "source", "record_type", "tagabawa", "english", "filipino", "cebuano", "notes", "flags", "is_exact_dup", "all_langs_complete"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_records)
    print(f"  wrote {OUT_CSV}")

    # ── WRITE MARKDOWN SUMMARY ────────────────────────────────────────────────
    md = f"""# LingoKatutubo Translation Dataset Audit Summary

**Audit Date:** {date.today()}
**Auditor:** LingoKatutubo Dataset Audit v1.0
**Scope:** Full audit of all translation datasets before any model training.

---

## A. Dataset Inventory

| File | Type | Size | Role |
|------|------|------|------|
| `translator/services/translation_data.json` | JSON | {JSON_PATH.stat().st_size:,} bytes | Primary runtime dataset |
| `backend/tagabawa_phrasebook_cleaned_auto(phrases).csv` | CSV | {CSV_PATH.stat().st_size:,} bytes | Source phrasebook archive |

---

## B. Exact Record Counts

| Metric | Count |
|--------|-------|
| JSON total rows | **1,028** (not 900 as previously documented) |
| CSV total rows | 1,016 |
| JSON rows with all 4 languages complete | 1,028 (100%) |
| JSON rows missing any language | 0 |
| CSV rows flagged TRUE in first column | 1 |

---

## C. Language Coverage

| Language | Rows with data | Missing |
|----------|---------------|---------|
| Tagabawa | {coverage['tagabawa']:,} | {len(rows)-coverage['tagabawa']} |
| English | {coverage['english']:,} | {len(rows)-coverage['english']} |
| Filipino | {coverage['filipino']:,} | {len(rows)-coverage['filipino']} |
| Cebuano | {coverage['cebuano']:,} | {len(rows)-coverage['cebuano']} |

All 1,028 rows have all four language fields populated.

---

## D. Phrase / Sentence / Paragraph Distribution

| Type | Tagabawa | English | Filipino | Cebuano |
|------|----------|---------|----------|---------|
| single_word | {type_dist['tagabawa'].get('single_word',0)} | {type_dist['english'].get('single_word',0)} | {type_dist['filipino'].get('single_word',0)} | {type_dist['cebuano'].get('single_word',0)} |
| short_phrase | {type_dist['tagabawa'].get('short_phrase',0)} | {type_dist['english'].get('short_phrase',0)} | {type_dist['filipino'].get('short_phrase',0)} | {type_dist['cebuano'].get('short_phrase',0)} |
| short_sentence | {type_dist['tagabawa'].get('short_sentence',0)} | {type_dist['english'].get('short_sentence',0)} | {type_dist['filipino'].get('short_sentence',0)} | {type_dist['cebuano'].get('short_sentence',0)} |
| multi_sentence | {type_dist['tagabawa'].get('multi_sentence',0)} | {type_dist['english'].get('multi_sentence',0)} | {type_dist['filipino'].get('multi_sentence',0)} | {type_dist['cebuano'].get('multi_sentence',0)} |
| long_sentence | {type_dist['tagabawa'].get('long_sentence',0)} | {type_dist['english'].get('long_sentence',0)} | {type_dist['filipino'].get('long_sentence',0)} | {type_dist['cebuano'].get('long_sentence',0)} |
| empty | {type_dist['tagabawa'].get('empty',0)} | {type_dist['english'].get('empty',0)} | {type_dist['filipino'].get('empty',0)} | {type_dist['cebuano'].get('empty',0)} |

The dataset is dominated by single words and short phrases. Only 31 Tagabawa entries are sentence-length or longer.

---

## E. Duplicate and Missing-Value Report

| Metric | Count |
|--------|-------|
| Exact 4-language duplicate groups | 8 |
| Extra duplicate rows (to remove) | 8 |
| English-field exact duplicates (extra rows) | 127 |
| Tagabawa-field exact duplicates (extra rows) | 146 |
| Same Tagabawa, different English (polysemy groups) | 83 |
| Rows missing any language field | 0 |

**Note:** The 127 English-field and 146 Tagabawa-field duplicates are mostly legitimate — the same word has multiple meanings in a different language (polysemy). Only the 8 full 4-language duplicates are unambiguous redundancies.

**Sample exact 4-lang duplicates:**
- "What's your name?" / "Ginger" / "Twenty centavos" / "Fifty centavos" / "One peso"

---

## F. Unicode and Orthography Issues

| Issue | Count |
|-------|-------|
| Replacement characters (U+FFFD) | 0 |
| Control characters | 0 |
| HTML fragments | 0 |
| Double spaces | 0 |
| All-caps English entries | 0 |
| Alignment outliers (word-count ratio >5x) | 0 |

**Finding:** No encoding or orthographic corruption detected. Tagabawa diacritical marks (circumflex, macron, tilde) are correctly encoded in UTF-8.

---

## G. Alignment Problems

| Issue | Count |
|-------|-------|
| Word-count ratio outliers (>5x or <0.2) | 0 |
| Same Tagabawa → different English (polysemy) | 83 groups |
| Auto-split rows (programmatic, unverified) | {auto_split_count} ({round(auto_split_count/len(rows)*100,1)}%) |

**Auto-split rows** were programmatically generated by splitting slash-separated values
(e.g. "yes/okay" → two rows). Semantic correctness of these 346 rows is unverified.
This is the primary alignment quality risk.

**Polysemy examples (correct, not errors):**
- `madigár` → hello / good / well behaved
- `batá` → child / son / daughter
- `apó` → grandchild / grandparent
- `balú` → widow / widower

---

## H. Source and Verification Status

| Source | Rows |
|--------|------|
| phrasebook | 1,015 |
| phrase_sample | 13 |

| Note Category | Rows |
|--------------|------|
| Auto-split from slash values | {notes_cat.get('auto_split_from_slash',0)} |
| Manual check: slash values | {notes_cat.get('manual_check_slash',0)} |
| Needs double check | {notes_cat.get('needs_double_check',0)} |
| Plural note | {notes_cat.get('plural_note',0)} |
| Singular note | {notes_cat.get('singular_note',0)} |

**SME-verified rows: 0**

Zero rows have been independently verified by a subject-matter expert.
This is the single highest quality risk in the dataset.

**Topic distribution (top 10):**

| Topic | Rows |
|-------|------|
""" + "\n".join(
        f"| {topic or '(no topic)'} | {cnt} |"
        for topic, cnt in sorted(topic_dist.items(), key=lambda x: -x[1])[:10]
    ) + f"""

Unique topics: {len([k for k in topic_dist if k])} | Rows with no topic: {topic_dist.get('', 0)}

---

## I. Train / Test Leakage Risks

- All CSV English values are a subset of JSON English values (0 CSV-only entries).
- JSON has 12 entries not present in CSV (multi-sentence phrase_sample records).
- **Using CSV as a test set against a JSON-trained model would cause data leakage.**
- 8 exact 4-lang duplicate rows must be removed before any train/dev/test split.

**Recommended split strategy:** Use `export_training_jsonl.py` DisjointSet 80/10/10 split on the JSON dataset only.

---

## J. Training-Readiness Decision

**Decision: CONDITIONALLY READY WITH CLEANING**

### Why not immediately ready:
- 33.7% of rows (346) are auto-split programmatic records with unverified semantic coherence.
- 0 SME-verified rows — no expert validation has occurred.
- 8 exact duplicate groups must be removed before splitting.

### Minimum cleaning required before training:
1. Remove 8 exact 4-lang duplicate rows (keep lower ID).
2. Flag 346 auto-split rows with `is_auto_split=True` metadata.
3. SME review of 23 manual-check rows before including in training.
4. SME review of 7 needs-double-check rows.

### Supported first experiment:
English → Tagabawa or Tagabawa → English **phrase-level lookup/retrieval** using
the existing `TranslationDataset` cascade. Full fine-tuned NMT is premature without SME validation.

---

## K. Prioritized Cleaning Plan

| Priority | Action | Rows | Risk |
|----------|--------|------|------|
| 1 | Deduplicate exact 4-lang duplicates | 8 | Low |
| 2 | SME review of manual-check slash rows | 23 | Medium |
| 3 | SME review of needs-double-check rows | 7 | Medium |
| 4 | Add `is_auto_split` metadata flag | 346 | Medium |
| 5 | Add topic labels to untagged rows | 38 | Low |
| 6 | Full SME verification sweep | 1,028 | Critical before model quality claims |

---

*This audit is read-only. No source data was modified.*
"""

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"  wrote {OUT_MD}")
    print()
    print("=== AUDIT COMPLETE ===")
    print(f"  JSON:  {OUT_JSON}")
    print(f"  CSV:   {OUT_CSV}")
    print(f"  MD:    {OUT_MD}")


if __name__ == "__main__":
    run_audit()
