from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from translator.models import SystemEventLog, TranslationJob
from translator.services import start_translation_job


class Command(BaseCommand):
    help = "Recover translation jobs abandoned in queued/retrying/processing states."

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=15,
            help="Minimum age in minutes since the last job update.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show stale jobs and log dry-run events without changing job status.",
        )
        parser.add_argument(
            "--retry-queued",
            action="store_true",
            help="Mark stale queued/retrying jobs as retrying and dispatch them again.",
        )

    def handle(self, *args, **options):
        minutes = max(1, int(options["minutes"]))
        dry_run = bool(options["dry_run"])
        retry_queued = bool(options["retry_queued"])
        cutoff = timezone.now() - timezone.timedelta(minutes=minutes)

        stale_jobs = TranslationJob.objects.filter(
            is_deleted=False,
            status__in=[
                TranslationJob.Status.QUEUED,
                TranslationJob.Status.RETRYING,
                TranslationJob.Status.PROCESSING,
            ],
            updated_at__lte=cutoff,
        ).select_related("owner").order_by("updated_at")

        count = 0
        for job in stale_jobs:
            count += 1
            action = self._action_for(job, retry_queued)
            message = (
                f"{action}: job={job.job_id} status={job.status} "
                f"updated_at={job.updated_at.isoformat()}"
            )
            self.stdout.write(message)
            self._log_recovery_event(job, action, dry_run, minutes)

            if dry_run:
                continue

            if action == "retry":
                self._mark_retrying(job, minutes)
                start_translation_job(job)
            else:
                self._mark_failed(job, minutes)

        if count == 0:
            self.stdout.write("No stale translation jobs found.")
        else:
            suffix = " (dry-run)" if dry_run else ""
            self.stdout.write(self.style.SUCCESS(f"Recovered {count} stale job(s){suffix}."))

    @staticmethod
    def _action_for(job: TranslationJob, retry_queued: bool) -> str:
        if retry_queued and job.status in {
            TranslationJob.Status.QUEUED,
            TranslationJob.Status.RETRYING,
        }:
            return "retry"
        return "fail"

    @staticmethod
    def _log_recovery_event(
        job: TranslationJob,
        action: str,
        dry_run: bool,
        minutes: int,
    ) -> None:
        SystemEventLog.objects.create(
            actor=job.owner,
            job=job,
            level=SystemEventLog.Level.WARNING,
            event_type="translation_job_stale_recovery",
            message=(
                f"{'Dry run: would ' if dry_run else ''}{action} stale "
                f"translation job after {minutes} minute(s)."
            ),
            metadata={
                "action": action,
                "dry_run": dry_run,
                "previous_status": job.status,
                "minutes": minutes,
                "updated_at": job.updated_at.isoformat(),
            },
        )

    @staticmethod
    def _mark_retrying(job: TranslationJob, minutes: int) -> None:
        metadata = dict(job.metadata or {})
        metadata["stale_recovery"] = {
            "action": "retry",
            "minutes": minutes,
            "recovered_at": timezone.now().isoformat(),
            "previous_status": job.status,
        }
        TranslationJob.objects.filter(id=job.id).update(
            status=TranslationJob.Status.RETRYING,
            current_phase="retrying",
            current_step="Retrying abandoned translation job",
            phase_message="This job was abandoned and has been queued for retry.",
            error="",
            progress=0,
            metadata=metadata,
            updated_at=timezone.now(),
        )
        job.status = TranslationJob.Status.RETRYING

    @staticmethod
    def _mark_failed(job: TranslationJob, minutes: int) -> None:
        now = timezone.now()
        metadata = dict(job.metadata or {})
        metadata["stale_recovery"] = {
            "action": "fail",
            "minutes": minutes,
            "recovered_at": now.isoformat(),
            "previous_status": job.status,
        }
        message = (
            "Translation job was abandoned before completion and was marked "
            "failed by stale-job recovery."
        )
        TranslationJob.objects.filter(id=job.id).update(
            status=TranslationJob.Status.FAILED,
            current_phase="failed",
            current_step="Translation failed",
            phase_message=message,
            error=message,
            metadata=metadata,
            updated_at=now,
            completed_at=now,
        )
