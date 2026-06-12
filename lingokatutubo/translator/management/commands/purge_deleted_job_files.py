from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from pathlib import Path
import os
import shutil

from translator.models import TranslationJob, UploadedDocument


def is_path_safe(path_str: str) -> bool:
    """Check if the path is safely nested inside settings.MEDIA_ROOT."""
    if not path_str:
        return False
    try:
        media_root = Path(settings.MEDIA_ROOT).resolve()
        candidate = Path(path_str).resolve()
        # candidate must be strictly a subdirectory or subfile of media_root
        return media_root in candidate.parents
    except Exception:
        return False


class Command(BaseCommand):
    help = "Purges physical files of soft-deleted translation jobs older than N days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Purge soft-deleted files deleted older than this many days.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print list of files that would be deleted, without actually deleting them.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        # Query jobs that are soft-deleted and whose deletion date is older than the cutoff
        jobs = TranslationJob.objects.filter(
            is_deleted=True,
            deleted_at__lte=cutoff,
        )

        self.stdout.write(f"Found {jobs.count()} soft-deleted jobs older than {days} days.")

        deleted_files_count = 0
        deleted_dirs_count = 0

        for job in jobs:
            # Safety check: Active jobs must never be deleted
            if job.status in {TranslationJob.Status.QUEUED, TranslationJob.Status.PROCESSING}:
                self.stdout.write(self.style.WARNING(f"Skipping active job: {job.id}"))
                continue

            self.stdout.write(f"Processing soft-deleted Job {job.id} (deleted at {job.deleted_at})")

            # Collate all candidate paths for this job
            candidate_paths = set()

            # 1. Job fields paths
            for field in ["input_file_path", "output_file_path", "bilingual_file_path", "structure_file_path", "upload_file_path"]:
                path_val = getattr(job, field, "")
                if path_val:
                    candidate_paths.add(path_val)

            # 2. Associated UploadedDocument file path
            try:
                doc = job.uploaded_document
                if doc and doc.file_path:
                    candidate_paths.add(doc.file_path)
            except UploadedDocument.DoesNotExist:
                pass
            except Exception:
                pass

            # 3. Job directory path
            from translator.services import job_directory_path
            try:
                job_dir = job_directory_path(job.job_id)
                if job_dir:
                    candidate_paths.add(job_dir)
            except Exception:
                pass

            # Verify and delete candidate paths
            for p_str in sorted(candidate_paths):
                if not is_path_safe(p_str):
                    self.stdout.write(self.style.WARNING(f"  Unsafe path skipped: {p_str}"))
                    continue

                p = Path(p_str).resolve()
                if not p.exists():
                    continue

                if p.is_file():
                    if dry_run:
                        self.stdout.write(f"  [DRY-RUN] Would delete file: {p}")
                    else:
                        try:
                            os.remove(p)
                            self.stdout.write(f"  Deleted file: {p}")
                            deleted_files_count += 1
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"  Failed to delete file {p}: {e}"))
                elif p.is_dir():
                    if dry_run:
                        self.stdout.write(f"  [DRY-RUN] Would delete directory: {p}")
                    else:
                        try:
                            shutil.rmtree(p)
                            self.stdout.write(f"  Deleted directory: {p}")
                            deleted_dirs_count += 1
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"  Failed to delete directory {p}: {e}"))

        mode = "Dry-run complete." if dry_run else "Purge complete."
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} Purged {deleted_files_count} files and {deleted_dirs_count} directories."
            )
        )
