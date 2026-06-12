import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from translator.models import DatasetImportBatch, PhrasebookEntry


LANGUAGE_FIELDS = ("english", "tagabawa", "filipino", "cebuano")


class Command(BaseCommand):
    help = "Import phrasebook rows from JSON or CSV into PhrasebookEntry."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            default="translator/services/translation_data.json",
            help="Path to translation_data JSON or CSV.",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Update existing entries matched by all language fields.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise CommandError(f"Phrasebook file not found: {path}")

        batch = DatasetImportBatch.objects.create(
            source_filename=str(path),
            status=DatasetImportBatch.Status.PROCESSING,
        )
        try:
            rows = self._load_rows(path)
            created = 0
            updated = 0
            failed = []
            for row_number, raw_row in enumerate(rows, start=1):
                row = self._normalize_row(raw_row)
                if not any(row.get(field) for field in LANGUAGE_FIELDS):
                    continue
                lookup = {field: row.get(field, "") for field in LANGUAGE_FIELDS}
                defaults = {
                    "topic": row.get("topic", ""),
                    "source": row.get("source", "phrasebook") or "phrasebook",
                    "notes": row.get("notes", ""),
                    "needs_review": _needs_review(row),
                    "metadata": {
                        key: value
                        for key, value in row.items()
                        if key not in {*LANGUAGE_FIELDS, "topic", "source", "notes"}
                    },
                }
                try:
                    if options["update"]:
                        _, was_created = PhrasebookEntry.objects.update_or_create(
                            **lookup,
                            defaults=defaults,
                        )
                        created += int(was_created)
                        updated += int(not was_created)
                    else:
                        PhrasebookEntry.objects.create(**lookup, **defaults)
                        created += 1
                except Exception as exc:
                    failed.append({"row": row_number, "error": str(exc)})

            batch.status = DatasetImportBatch.Status.COMPLETED if not failed else DatasetImportBatch.Status.FAILED
            batch.rows_total = len(rows)
            batch.rows_created = created
            batch.rows_updated = updated
            batch.rows_failed = len(failed)
            batch.error_report = failed
            batch.completed_at = timezone.now()
            batch.save()
        except Exception:
            batch.status = DatasetImportBatch.Status.FAILED
            batch.completed_at = timezone.now()
            batch.save(update_fields=["status", "completed_at", "updated_at"])
            raise

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {created} phrasebook rows; updated {updated}; failed {len(failed)}."
            )
        )

    def _load_rows(self, path: Path) -> list[dict]:
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))

        with path.open("r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            for key in ("rows", "entries", "data", "phrases"):
                rows = raw.get(key)
                if isinstance(rows, list):
                    return rows
        raise CommandError("Unsupported phrasebook JSON shape.")

    def _normalize_row(self, row: dict) -> dict:
        normalized = {str(key).strip().lower(): value for key, value in row.items()}
        return {
            "english": _clean(normalized.get("english") or normalized.get("english_source")),
            "tagabawa": _clean(
                normalized.get("tagabawa")
                or normalized.get("bagobo")
                or normalized.get("tagabawa_source")
            ),
            "filipino": _clean(
                normalized.get("filipino")
                or normalized.get("tagalog")
                or normalized.get("filipino_source")
            ),
            "cebuano": _clean(normalized.get("cebuano") or normalized.get("cebuano_source")),
            "topic": _clean(normalized.get("topic")),
            "source": _clean(normalized.get("source")) or "phrasebook",
            "notes": _clean(normalized.get("notes")),
            **{
                key: value
                for key, value in normalized.items()
                if key not in {"english", "tagabawa", "bagobo", "filipino", "tagalog", "cebuano"}
            },
        }


def _clean(value) -> str:
    return str(value or "").strip()


def _needs_review(row: dict) -> bool:
    notes = _clean(row.get("notes")).lower()
    return "manual" in notes or "double check" in notes or "review" in notes
