"""
Dataset engineer script - DEMO-SAFE version.

Safety guarantees:
  1. Creates translation_data.json.bak before any write.
  2. Skips entries whose Tagabawa value contains [UNKNOWN_FOR_REVIEW].
  3. Skips entries with confidence != "high" (parsed from notes field).
  4. Skips entries whose English phrase already exists in the dataset
     (duplicate detection by exact, case-insensitive English match).
  5. Verifies every source_id listed in the notes field exists in the dataset.
  6. Preserves the existing JSON schema exactly (id, topic, tagabawa, english,
     filipino, cebuano, source, notes).
  7. Prints a full run report at the end.

Run:
  python add_phrase_samples.py
"""

import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATASET_PATH = Path("translation_data.json")
BACKUP_PATH  = Path("translation_data.json.bak")

UNKNOWN_MARKER = "[UNKNOWN_FOR_REVIEW]"

# ---------------------------------------------------------------------------
# Candidate phrase entries
# ---------------------------------------------------------------------------
# Schema keys: topic, tagabawa, english, filipino, cebuano, source, notes
#
# Notes format (machine-readable):
#   phrase_sample|source_ids:<comma-list>|confidence:<high|medium|low>|word_map:...
#
# Only entries with confidence:high AND no [UNKNOWN_FOR_REVIEW] will be added.
# All source IDs are verified to exist in the dataset at runtime.

CANDIDATE_PHRASES = [
    # -------------------------------------------------------------------------
    # Phrase 1 — "Hello, come in."
    # Sources: ID 2 (Hello → Madigár) + ID 6 (Come in → Ahu kó)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Madigár. Ahu kó.",
        "english":  "Hello, come in.",
        "filipino": "Kamusta. Pasok po kayo.",
        "cebuano":  "Maayo. Sulod mo.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:2,6|confidence:high"
                    "|word_map:Hello=Madigár(id2),come_in=Ahu_kó(id6)",
    },
    # -------------------------------------------------------------------------
    # Phrase 2 — "Sit down, please."
    # SKIPPED — "please" → [UNKNOWN_FOR_REVIEW], confidence:medium
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Unsad kó. [UNKNOWN_FOR_REVIEW].",
        "english":  "Sit down, please.",
        "filipino": "Maupo kayo, pakisuyo.",
        "cebuano":  "Lingkod mo, palihug.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:7|confidence:medium"
                    "|word_map:sit_down=Unsad_kó(id7),please=UNKNOWN_FOR_REVIEW",
    },
    # -------------------------------------------------------------------------
    # Phrase 3 — "What's your name?"
    # Source: ID 87 (exact phrase)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Sadanné ngadan nu?",
        "english":  "What's your name?",
        "filipino": "Anong pangalan mo?",
        "cebuano":  "Kinsa imong ngalan?",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:87|confidence:high"
                    "|word_map:full_exact_phrase(id87)",
    },
    # -------------------------------------------------------------------------
    # Phrase 4 — "Thank you."
    # Source: ID 710 (exact phrase)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Salamat.",
        "english":  "Thank you.",
        "filipino": "Salamat po.",
        "cebuano":  "Salamat.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:710|confidence:high"
                    "|word_map:full_exact_phrase(id710)",
    },
    # -------------------------------------------------------------------------
    # Phrase 5 — "I'm sick. Go to the doctor."
    # Sources: ID 305 + ID 306 (exact phrases)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Ágkabágákanna. Pahá ka tut duktur.",
        "english":  "I'm sick. Go to the doctor.",
        "filipino": "May sakit ako. Magpatingin ka sa doktor.",
        "cebuano":  "Nagsakit ko. Magpatan-aw ka sa doktor.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:305,306|confidence:high"
                    "|word_map:I'm_sick=Ágkabágákanna(id305),"
                    "go_to_doctor=Pahá_ka_tut_duktur(id306)",
    },
    # -------------------------------------------------------------------------
    # Phrase 6 — "I am hungry and thirsty."
    # Source: ID 494 (exact phrase)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Ágballusánna asta ágkatákkanganna.",
        "english":  "I am hungry and thirsty.",
        "filipino": "Nagugutom ako at nauuhaw.",
        "cebuano":  "Gigutom ko ug giuhaw.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:494|confidence:high"
                    "|word_map:full_exact_phrase(id494)",
    },
    # -------------------------------------------------------------------------
    # Phrase 7 — "Can you help me? I don't know."
    # Sources: ID 92 + ID 55 (exact phrases)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Makatabang ka kanak? Ándà sóddór ku.",
        "english":  "Can you help me? I don't know.",
        "filipino": "Matutulungan mo ba ako? Hindi ko alam.",
        "cebuano":  "Makatabang ka ba sa akoa? Wala ko kahibalo.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:92,55|confidence:high"
                    "|word_map:can_you_help_me=Makatabang_ka_kanak(id92),"
                    "i_dont_know=Ándà_sóddór_ku(id55)",
    },
    # -------------------------------------------------------------------------
    # Phrase 8 — "Come here. Sit down."
    # Sources: ID 61 + ID 7 (exact phrases)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Sadun ka dini. Unsad kó.",
        "english":  "Come here. Sit down.",
        "filipino": "Halika rito. Maupo kayo.",
        "cebuano":  "Dali diri. Lingkod mo.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:61,7|confidence:high"
                    "|word_map:come_here=Sadun_ka_dini(id61),"
                    "sit_down=Unsad_kó(id7)",
    },
    # -------------------------------------------------------------------------
    # Phrase 9 — "Today is a good day."
    # SKIPPED — copula "is a" → [UNKNOWN_FOR_REVIEW], confidence:low
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Nigá [UNKNOWN_FOR_REVIEW] madigár állá.",
        "english":  "Today is a good day.",
        "filipino": "Ngayon ay isang magandang araw.",
        "cebuano":  "Karon maayo nga adlaw.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:751,213,747|confidence:low"
                    "|word_map:today=nigá(id751),good=madigár(id213),"
                    "day=állá(id747),is_a=UNKNOWN_FOR_REVIEW(grammatical_connector)",
    },
    # -------------------------------------------------------------------------
    # Phrase 10 — "I'll be going home now. Let's go."
    # Sources: ID 22 + ID 23 (exact phrases)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Mulì ad. Panó kid.",
        "english":  "I'll be going home now. Let's go.",
        "filipino": "Uuwi na ako. Tayo na.",
        "cebuano":  "Mouli nako. Adto na ta.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:22,23|confidence:high"
                    "|word_map:going_home=Mulì_ad(id22),lets_go=Panó_kid(id23)",
    },
    # -------------------------------------------------------------------------
    # Phrase 11 — "I just came to visit. Come with me."
    # Sources: ID 94 + ID 68 (exact phrases)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Gahà a dád. Tákkás ka kanak.",
        "english":  "I just came to visit. Come with me.",
        "filipino": "Dumadalaw lang ako. Sumama ka sa akin.",
        "cebuano":  "Nagbisita lang ko. Uban sa akoa.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:94,68|confidence:high"
                    "|word_map:came_to_visit=Gahà_a_dád(id94),"
                    "come_with_me=Tákkás_ka_kanak(id68)",
    },
    # -------------------------------------------------------------------------
    # Phrase 12 — "Are you sick? He is well now."
    # Sources: ID 304 + ID 79 (exact phrases)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Ágkabágákan ka? Madigár dán.",
        "english":  "Are you sick? He is well now.",
        "filipino": "May sakit ka ba? Magaling na s'ya.",
        "cebuano":  "Nagsakit ka? Maayo na.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:304,79|confidence:high"
                    "|word_map:are_you_sick=Ágkabágákan_ka(id304),"
                    "well_now=Madigár_dán(id79)",
    },
    # -------------------------------------------------------------------------
    # Phrase 13 — "I am hungry. I'd like to eat now."
    # Sources: ID 319 (hungry) + ID 83 (I'd like to eat now)
    # -------------------------------------------------------------------------
    {
        "topic":    "phrase_samples",
        "tagabawa": "Ágballusán. Kadigárrannad kuman.",
        "english":  "I am hungry. I'd like to eat now.",
        "filipino": "Nagugutom ako. Gusto ko nang kumain.",
        "cebuano":  "Gigutum. Gusto ko mokaon na.",
        "source":   "phrase_sample",
        "notes":    "phrase_sample|source_ids:319,83|confidence:high"
                    "|word_map:hungry=Ágballusán(id319),"
                    "want_to_eat=Kadigárrannad_kuman(id83)",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_confidence(notes: str) -> str:
    """Extract confidence level from notes string. Returns 'unknown' if absent."""
    for part in notes.split("|"):
        if part.startswith("confidence:"):
            return part.split(":", 1)[1].strip().lower()
    return "unknown"


def _parse_source_ids(notes: str) -> list[int]:
    """Extract the list of source IDs from notes string."""
    for part in notes.split("|"):
        if part.startswith("source_ids:"):
            raw = part.split(":", 1)[1].strip()
            ids = []
            for token in raw.split(","):
                token = token.strip()
                if token.isdigit():
                    ids.append(int(token))
            return ids
    return []


def _verify_source_ids(
    source_ids: list[int], dataset_id_set: set[int]
) -> tuple[bool, list[int]]:
    """Return (all_present, list_of_missing_ids)."""
    missing = [sid for sid in source_ids if sid not in dataset_id_set]
    return (len(missing) == 0), missing


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Ensure UTF-8 output on Windows consoles that default to cp1252
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # ------------------------------------------------------------------
    # 1. Load dataset
    # ------------------------------------------------------------------
    if not DATASET_PATH.exists():
        print(f"ERROR: Dataset not found at '{DATASET_PATH}'.")
        sys.exit(1)

    with DATASET_PATH.open("r", encoding="utf-8-sig") as fh:
        data = json.load(fh)

    if not isinstance(data, dict) or "rows" not in data:
        print("ERROR: Unexpected dataset format — expected a dict with a 'rows' key.")
        sys.exit(1)

    rows: list[dict] = data["rows"]
    rows_before = len(rows)

    # ------------------------------------------------------------------
    # 2. Build lookup structures from existing dataset
    # ------------------------------------------------------------------
    existing_english_lower: set[str] = {
        r.get("english", "").strip().lower() for r in rows
    }
    existing_id_set: set[int] = {r.get("id", -1) for r in rows}
    max_id: int = max(existing_id_set)

    # ------------------------------------------------------------------
    # 3. Create backup BEFORE any modification
    # ------------------------------------------------------------------
    shutil.copy2(DATASET_PATH, BACKUP_PATH)
    backup_created = True

    # ------------------------------------------------------------------
    # 4. Filter candidates
    # ------------------------------------------------------------------
    added: list[dict] = []
    skipped_unknown: list[str] = []
    skipped_low_confidence: list[str] = []
    skipped_duplicate: list[str] = []
    skipped_bad_source: list[str] = []

    for candidate in CANDIDATE_PHRASES:
        english  = candidate.get("english", "").strip()
        tagabawa = candidate.get("tagabawa", "").strip()
        notes    = candidate.get("notes", "")

        # Rule 3: skip if Tagabawa contains [UNKNOWN_FOR_REVIEW]
        if UNKNOWN_MARKER in tagabawa:
            skipped_unknown.append(english)
            continue

        # Rule 4: skip if confidence is not "high"
        confidence = _parse_confidence(notes)
        if confidence != "high":
            skipped_low_confidence.append(f"{english!r} (confidence:{confidence})")
            continue

        # Rule 1: skip duplicate (match on English text, case-insensitive)
        if english.lower() in existing_english_lower:
            skipped_duplicate.append(english)
            continue

        # Rule 5: verify all source IDs exist in the dataset
        source_ids = _parse_source_ids(notes)
        all_present, missing = _verify_source_ids(source_ids, existing_id_set)
        if not all_present:
            skipped_bad_source.append(
                f"{english!r} — missing source IDs: {missing}"
            )
            continue

        # Passed all filters — assign ID and queue for insertion
        max_id += 1
        row = dict(candidate)
        row["id"] = max_id
        # Enforce key order to match existing schema
        ordered = {
            "id":       row["id"],
            "topic":    row.get("topic", "phrase_samples"),
            "tagabawa": row["tagabawa"],
            "english":  row["english"],
            "filipino": row.get("filipino", ""),
            "cebuano":  row.get("cebuano", ""),
            "source":   row.get("source", "phrase_sample"),
            "notes":    row.get("notes", ""),
        }
        added.append(ordered)
        # Keep duplicate-detection set current for the rest of the loop
        existing_english_lower.add(english.lower())
        existing_id_set.add(max_id)

    # ------------------------------------------------------------------
    # 5. Append and write (only if there is something to add)
    # ------------------------------------------------------------------
    if added:
        rows.extend(added)
        with DATASET_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    rows_after = len(rows)

    # ------------------------------------------------------------------
    # 6. Report
    # ------------------------------------------------------------------
    print("=" * 62)
    print("  LINGOKATUTUBO — Demo-Safe Phrase Sample Injection Report")
    print("=" * 62)
    print()
    print(f"  Rows before          : {rows_before}")
    print(f"  Rows after           : {rows_after}")
    print(f"  Entries added        : {len(added)}")
    print(f"  Backup path          : {BACKUP_PATH.resolve()}")
    print()

    if added:
        print("  [ADDED] ADDED ENTRIES:")
        for row in added:
            print(f"     ID {row['id']:4d} | {row['english']!r}")
    else:
        print("  [OK] No new entries to add.")

    print()
    if skipped_duplicate:
        print(f"  [SKIP] DUPLICATE - skipped ({len(skipped_duplicate)}):")
        for s in skipped_duplicate:
            print(f"     · {s!r}")

    if skipped_unknown:
        print(f"  [WARN] UNKNOWN_FOR_REVIEW - skipped ({len(skipped_unknown)}):")
        for s in skipped_unknown:
            print(f"     · {s!r}")

    if skipped_low_confidence:
        print(f"  [SKIP] LOW CONFIDENCE - skipped ({len(skipped_low_confidence)}):")
        for s in skipped_low_confidence:
            print(f"     · {s}")

    if skipped_bad_source:
        print(f"  [FAIL] BAD SOURCE IDS - skipped ({len(skipped_bad_source)}):")
        for s in skipped_bad_source:
            print(f"     · {s}")

    print()
    print("=" * 62)


if __name__ == "__main__":
    main()
