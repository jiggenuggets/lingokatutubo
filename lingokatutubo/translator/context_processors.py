from .models import TranslationJob


ACTIVE_JOB_STATUSES = (
    TranslationJob.Status.QUEUED,
    TranslationJob.Status.PROCESSING,
    TranslationJob.Status.RETRYING,
)


def active_translation_jobs(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    active_jobs = TranslationJob.objects.filter(
        owner=user,
        is_deleted=False,
        status__in=ACTIVE_JOB_STATUSES,
    ).order_by("created_at")

    return {
        "active_translation_job_count": active_jobs.count(),
        "active_translation_job": active_jobs.first(),
    }
