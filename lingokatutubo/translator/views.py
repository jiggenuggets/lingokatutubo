import hashlib
import json
import os
import re
import copy
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Avg, Count, Prefetch, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.csrf import csrf_failure as _django_csrf_failure
from django.views.decorators.http import require_http_methods, require_POST

from .forms import DocumentUploadForm, SignUpForm
from .models import (
    GeneratedOutput,
    OCRResult,
    SystemEventLog,
    TranslationJob,
    TranslationSegment,
    UploadedDocument,
    UserActivityLog,
)
from .services import (
    detect_upload_file_type,
    job_directory_path,
    job_structure_path,
    quick_translate_text,
    save_uploaded_file,
    start_translation_job,
    translation_dataset_loaded,
)
from .services.display_utils import clean_invisible_unicode
from .services.translation_display import get_display_translation, get_display_translation_text


PREVIEW_IMAGE_NAME_RE = re.compile(r"^(?:original|translated)_page_\d+\.png$")

# Shown to every user (staff included) in the prominent failure banner and the
# JSON status API. Raw backend exception text (codec errors, tracebacks, file
# paths, model paths) must never reach this surface — it only ever appears
# inside the staff-only technical details panel on the job detail page.
GENERIC_TRANSLATION_FAILURE_MESSAGE = (
    "Translation could not be completed. Please try another file or contact the administrator."
)

# Path prefixes the frontend only ever talks to via fetch() + response.json().
# Auth/CSRF/permission failures on these must stay JSON — a redirect or an
# HTML error page here breaks response.json() with "Unexpected token '<'".
JSON_API_PATH_PREFIXES = (
    "/translate/upload/",
    "/translate/status/",
    "/translate/structure/",
    "/translate/preview-data/",
    "/api/",
)


def csrf_failure(request, reason=""):
    """CSRF failure handler (see CSRF_FAILURE_VIEW in settings.py).

    JSON API routes get a JSON body so fetch() callers can show a clean
    message instead of failing to parse Django's HTML CSRF error page.
    Normal HTML form posts (login, signup, delete-confirm) keep Django's
    default CSRF error page unchanged.
    """
    if request.path.startswith(JSON_API_PATH_PREFIXES):
        return JsonResponse(
            {
                "ok": False,
                "error": "Your session expired. Please refresh the page and try again.",
            },
            status=403,
        )
    return _django_csrf_failure(request, reason=reason)


def api_login_required(view_func):
    """Like @login_required, but for JSON API endpoints.

    @login_required redirects to the HTML login page; fetch() follows that
    redirect automatically and ends up handing response.json() an HTML
    document, which throws "Unexpected token '<'". API routes need a JSON
    body instead so the frontend can show a clean message.
    """

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Please log in again to continue.",
                    "redirect_url": reverse("translator:login"),
                },
                status=401,
            )
        return view_func(request, *args, **kwargs)

    return wrapped


def home(request):
    return render(request, "translator/home.html")


def signup(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("translator:translate")
    else:
        form = SignUpForm()
    return render(request, "registration/signup.html", {"form": form})


class TranslatorLoginView(auth_views.LoginView):
    """Login view that tailors its heading when sent here to reach Translate.

    `@login_required` on `translate()` redirects here with `next=/translate/`;
    the template swaps its heading/subtext for that case instead of using a
    flash message, since the destination is already implied by `next`.
    """

    template_name = "registration/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_is_translate"] = self.get_redirect_url() == reverse("translator:translate")
        return context


@login_required
def translate(request):
    recent_query = (
        _owned_jobs_queryset(request)
        .select_related("uploaded_document")
        .annotate(
            segment_count=Count("segments", distinct=True),
            review_segment_count=Count(
                "segments",
                filter=Q(segments__needs_review=True),
                distinct=True,
            ),
            translation_confidence=Avg("segments__confidence"),
            ocr_confidence=Avg("ocr_results__confidence"),
            page_count=Count("pages", distinct=True),
        )
        .prefetch_related("generated_outputs", "pages", "ocr_results")
    )

    recent_jobs = [_job_card_context(job) for job in recent_query[:5]]

    active_job = next(
        (
            card
            for card in recent_jobs
            if card["job"].status
            in {
                TranslationJob.Status.QUEUED,
                TranslationJob.Status.RETRYING,
                TranslationJob.Status.PROCESSING,
            }
        ),
        None,
    )

    active_state = active_job or {
        "job": None,
        "can_preview": False,
        "can_download": False,
        "is_processing": False,
        "has_failed": False,
        "can_retry": False,
    }

    return render(
        request,
        "translator/translate.html",
        {
            "upload_form": DocumentUploadForm(),
            "recent_jobs": recent_jobs,
            "active_job": active_job,
            "can_preview": active_state["can_preview"],
            "can_download": active_state["can_download"],
            "is_processing": active_state["is_processing"],
            "has_failed": active_state["has_failed"],
        },
    )


@login_required
def history(request):
    search_query = request.GET.get("q", "").strip()
    selected_status = request.GET.get("status", "").strip().lower()

    jobs = (
        _owned_jobs_queryset(request)
        .select_related("uploaded_document")
        .annotate(
            segment_count=Count("segments", distinct=True),
            review_segment_count=Count(
                "segments",
                filter=Q(segments__needs_review=True),
                distinct=True,
            ),
            translation_confidence=Avg("segments__confidence"),
            ocr_confidence=Avg("ocr_results__confidence"),
            page_count=Count("pages", distinct=True),
            output_count=Count("generated_outputs", distinct=True),
        )
        .prefetch_related("generated_outputs", "pages", "ocr_results")
        .order_by("-created_at")
    )

    if search_query:
        jobs = jobs.filter(
            Q(original_filename__icontains=search_query)
            | Q(uploaded_document__original_filename__icontains=search_query)
        )

    valid_statuses = {value for value, _label in TranslationJob.Status.choices}

    if selected_status in valid_statuses:
        jobs = jobs.filter(status=selected_status)

    return render(
        request,
        "translator/history.html",
        {
            "jobs": [_job_card_context(job) for job in jobs],
            "filters": TranslationJob.Status.choices,
            "search_query": search_query,
            "selected_status": selected_status if selected_status in valid_statuses else "",
        },
    )


@login_required
def job_detail(request, job_id):
    job = (
        _owned_jobs_queryset(request)
        .select_related("uploaded_document")
        .annotate(
            segment_count=Count("segments", distinct=True),
            review_segment_count=Count(
                "segments",
                filter=Q(segments__needs_review=True),
                distinct=True,
            ),
            translation_confidence=Avg("segments__confidence"),
            ocr_confidence=Avg("ocr_results__confidence"),
            page_count=Count("pages", distinct=True),
        )
        .prefetch_related("generated_outputs", "pages", "ocr_results")
        .filter(id=job_id)
        .first()
    )

    if not job:
        raise Http404("Job not found")

    context = _job_card_context(job)
    # Suppress the global active-job banner when we're already viewing this job's detail page
    context["hide_active_job_banner"] = context["is_processing"]

    if job.status == TranslationJob.Status.COMPLETED:
        # Show the "ready" message once per job per session — not on every
        # reload of an already-acknowledged completed job.
        notified_key = f"job_ready_notified_{job.id}"
        if not request.session.get(notified_key):
            messages.success(request, "Translated file is ready.")
            request.session[notified_key] = True

    return render(request, "translator/job_detail.html", context)


PREVIEW_SEGMENTS_PER_PAGE = 25


@login_required
def preview(request, job_id):
    job = (
        _owned_jobs_queryset(request)
        .prefetch_related(
            "ocr_results",
            "generated_outputs",
            "pages",
        )
        .select_related("uploaded_document")
        .annotate(
            segment_count=Count("segments", distinct=True),
            review_segment_count=Count(
                "segments",
                filter=Q(segments__needs_review=True),
                distinct=True,
            ),
            translation_confidence=Avg("segments__confidence"),
            ocr_confidence=Avg("ocr_results__confidence"),
            page_count=Count("pages", distinct=True),
        )
        .filter(id=job_id)
        .first()
    )

    if not job:
        raise Http404("Job not found")

    state = _job_card_context(job)

    if not state["can_preview"]:
        if state["has_failed"]:
            messages.error(request, state["failure_message"])
        elif state["is_processing"]:
            messages.info(
                request,
                "The bilingual preview will be available after processing is complete.",
            )
        else:
            messages.info(request, "The bilingual preview is not available yet.")

        return redirect("translator:job_detail", job_id=job.id)

    segments_qs = (
        TranslationSegment.objects.filter(job=job)
        .select_related("page")
        .order_by("segment_index")
    )
    paginator = Paginator(segments_qs, PREVIEW_SEGMENTS_PER_PAGE)
    try:
        page_number = int(request.GET.get("page", 1))
    except (ValueError, TypeError):
        page_number = 1
    segments_page = paginator.get_page(page_number)
    for segment in segments_page.object_list:
        segment.display_source_text = clean_invisible_unicode(segment.source_text)
        segment.display_translated_text = get_display_translation(segment)

    return render(
        request,
        "translator/preview.html",
        {
            **state,
            "uploaded_document": getattr(job, "uploaded_document", None),
            "document_pages": list(job.pages.all()),
            "ocr_result": job.ocr_results.first(),
            "segments": segments_page,
            "total_segments": paginator.count,
            "generated_outputs": list(job.generated_outputs.all()),
            "ocr_summary": _ocr_summary(job),
            "image_preview": _image_preview_context(job),
            "thumbnail_url": state.get("thumbnail_url", ""),
            "status_label": state.get("status_label", _status_label(job.status)),
            "status_class": state.get("status_class", _status_class(job.status)),
        },
    )


@api_login_required
@require_POST
def api_translate(request):
    # 1. Active jobs limit check (Maximum 2 queued/processing jobs per user)
    active_jobs = TranslationJob.objects.filter(
        owner=request.user,
        status__in=[
            TranslationJob.Status.QUEUED,
            TranslationJob.Status.RETRYING,
            TranslationJob.Status.PROCESSING,
        ],
        is_deleted=False
    ).count()
    if active_jobs >= 2:
        return JsonResponse(
            {"detail": "You have reached the upload limit. Please wait before submitting another document."},
            status=400
        )

    # 2. Hourly upload attempts check (Maximum 5 attempts per hour)
    from django.core.cache import cache
    cache_key = f"upload_attempts_hourly_{request.user.id}"
    attempts = cache.get(cache_key, 0)
    if attempts >= 5:
        return JsonResponse(
            {"detail": "You have reached the upload limit. Please wait before submitting another document."},
            status=400
        )
    # Increment counter
    cache.set(cache_key, attempts + 1, timeout=3600)

    form = DocumentUploadForm(request.POST, request.FILES)

    if not form.is_valid():
        return JsonResponse({"detail": form.errors.get_json_data()}, status=400)

    uploaded = form.cleaned_data["file"]
    file_type = detect_upload_file_type(uploaded.name)

    if not file_type:
        return JsonResponse({"detail": "Unsupported file type."}, status=400)

    job = None
    try:
        job = TranslationJob.objects.create(
            owner=request.user,
            original_filename=uploaded.name,
            file_type=file_type,
            source_language=form.cleaned_data["source_language"],
            target_language=form.cleaned_data["target_language"],
            ocr_languages=form.cleaned_data.get("ocr_languages", ""),
            phase_message="Document is waiting to be processed.",
        )

        stored_paths = save_uploaded_file(uploaded, job.job_id)

        job.input_file_path = stored_paths["input_file_path"]
        job.upload_file_path = stored_paths["upload_file_path"]
        job.save(update_fields=["input_file_path", "upload_file_path", "updated_at"])

        UploadedDocument.objects.create(
            owner=request.user,
            job=job,
            original_filename=uploaded.name,
            file_type=file_type,
            file_path=stored_paths["upload_file_path"],
            file_size_bytes=uploaded.size,
            checksum_sha256=_sha256_file(stored_paths["upload_file_path"]),
            metadata={
                "content_type": getattr(uploaded, "content_type", ""),
                "source_language": job.source_language,
                "target_language": job.target_language,
            },
        )

        job.metadata = {
            **(job.metadata or {}),
            "original_content_type": getattr(uploaded, "content_type", ""),
            "original_file_size_bytes": uploaded.size,
        }
        job.save(update_fields=["metadata", "updated_at"])

        UserActivityLog.objects.create(
            user=request.user,
            action="upload_document",
            object_type="TranslationJob",
            object_id=job.job_id,
            metadata={"filename": uploaded.name, "file_type": file_type},
        )

        SystemEventLog.objects.create(
            actor=request.user,
            job=job,
            event_type="translation_job_created",
            message="Translation job created from document upload.",
            metadata={"filename": uploaded.name, "file_type": file_type},
        )

        start_translation_job(job)

        payload = _job_payload(job)
        payload["message"] = "Translation started"

        return JsonResponse(payload, status=202)
    except Exception as exc:
        SystemEventLog.objects.create(
            actor=request.user,
            job=job,
            level=SystemEventLog.Level.ERROR,
            event_type="upload_request_failed",
            message=f"Upload request failed: {exc}",
            metadata={"filename": uploaded.name},
        )
        error_payload = {"ok": False, "error": GENERIC_TRANSLATION_FAILURE_MESSAGE}
        if request.user.is_staff:
            error_payload["technical_detail"] = str(exc)
        return JsonResponse(error_payload, status=500)


@api_login_required
def api_job_status(request, job_id):
    try:
        job = _get_owned_job(request, job_id)
    except Http404:
        return JsonResponse({"ok": False, "error": "Job not found."}, status=404)
    return JsonResponse(_job_payload(job))


@api_login_required
def api_structure(request, job_id):
    try:
        job = _get_owned_job(request, job_id)
    except Http404:
        return JsonResponse({"ok": False, "error": "Job not found."}, status=404)

    structure_path = _resolve_job_file(
        job,
        job.structure_file_path or _default_structure_path(job),
    )

    if not structure_path or not structure_path.exists():
        return JsonResponse(
            {
                "error": "Structure not yet available",
                "detail": f"Job status is {job.status}.",
            },
            status=404,
        )

    try:
        with open(structure_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        return JsonResponse(
            {"error": "Failed to read structure.json", "detail": str(exc)},
            status=500,
        )

    data["status"] = job.status
    data = _display_safe_structure(data)

    return JsonResponse(data)


@api_login_required
def api_preview(request, job_id):
    try:
        job = _get_owned_job(request, job_id)
    except Http404:
        return JsonResponse({"ok": False, "error": "Job not found."}, status=404)

    if job.status != TranslationJob.Status.COMPLETED:
        return JsonResponse(
            {"error": f"Job status is {job.status}, not completed"},
            status=400,
        )

    metadata = job.metadata or {}
    translation_quality = _translation_quality_context(job)

    original_pages = [
        _preview_image_url(job, item)
        for item in metadata.get("preview_original", [])
        if _preview_image_url(job, item)
    ]

    translated_pages = [
        _preview_image_url(job, item)
        for item in metadata.get("preview_translated", [])
        if _preview_image_url(job, item)
    ]
    document_page_count = max(
        _metadata_document_page_count(metadata),
        int(getattr(job, "page_count", 0) or job.pages.count()),
        len(original_pages),
        len(translated_pages),
    )
    bilingual_first_page = _display_safe_bilingual_first_page(
        metadata.get("bilingual_first_page", {"blocks": []})
    )

    return JsonResponse(
        {
            "job_id": job.job_id,
            "left_page_preview": original_pages[0] if original_pages else None,
            "bilingual_first_page": bilingual_first_page,
            "original_pages": original_pages,
            "translated_pages": translated_pages,
            "page_count": document_page_count,
            "document_page_count": document_page_count,
            "previewed_page_count": max(len(original_pages), len(translated_pages)),
            **translation_quality,
            "translation_summary": translation_quality,
            "segments": [
                {
                    "segment_index": segment.segment_index,
                    "source_text": clean_invisible_unicode(segment.source_text),
                    "translated_text": get_display_translation(segment),
                    "display_translated_text": get_display_translation(segment),
                    "method": segment.method,
                    "confidence": segment.confidence,
                    "needs_review": segment.needs_review,
                    "page_number": segment.page.page_number if segment.page else None,
                }
                for segment in job.segments.order_by("segment_index")
            ],
        }
    )


@login_required
def preview_image(request, job_id, image_name):
    job = _get_owned_job(request, job_id)

    if not PREVIEW_IMAGE_NAME_RE.fullmatch(image_name):
        return JsonResponse({"error": "Invalid preview image name"}, status=400)

    job_dir = Path(job_directory_path(job.job_id)).resolve()

    if not job_dir.exists():
        raise Http404("Job files not found")

    image_path = (job_dir / "preview" / image_name).resolve()

    try:
        common_path = os.path.commonpath([str(job_dir), str(image_path)])
    except ValueError:
        return JsonResponse({"error": "Access denied"}, status=403)

    if common_path != str(job_dir) or not image_path.exists():
        raise Http404("Image not found")

    return FileResponse(open(image_path, "rb"), content_type="image/png")


@login_required
def download_job(request, job_id):
    job = _get_owned_job(request, job_id)
    state = _job_state_context(job)

    if not state["can_download"]:
        if job.status != TranslationJob.Status.COMPLETED:
            messages.info(
                request,
                "The translated PDF will be available after processing is complete.",
            )
        else:
            messages.error(request, "The translated PDF is not available for this job.")

        return redirect("translator:job_detail", job_id=job.id)

    requested_format = request.GET.get("format", "translated")

    if requested_format == "bilingual":
        resolved_path = _resolve_job_file(job, job.bilingual_file_path)
    else:
        resolved_path = _translated_output_path(job)

    if not resolved_path or not resolved_path.exists():
        messages.error(request, "The translated PDF is missing or no longer available.")
        return redirect("translator:job_detail", job_id=job.id)

    filename_prefix = "bilingual" if requested_format == "bilingual" else "translated"

    return FileResponse(
        open(resolved_path, "rb"),
        as_attachment=True,
        filename=f"{filename_prefix}_{job.job_id[:8]}.pdf",
        content_type="application/pdf",
    )


@require_http_methods(["GET", "POST"])
def quick_translate(request):
    text = request.POST.get("text") or request.GET.get("text") or ""

    if not text.strip():
        return JsonResponse({"error": "Text is required."}, status=400)

    source_language = (
        request.POST.get("source_language")
        or request.GET.get("source_language")
        or "auto"
    )

    target_language = (
        request.POST.get("target_language")
        or request.GET.get("target_language")
        or "tagabawa"
    )

    try:
        return JsonResponse(quick_translate_text(text, source_language, target_language))
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@staff_member_required(login_url="translator:login")
def health(request):
    return render(
        request,
        "translator/health.html",
        {
            "checks": _health_checks(),
            "job_counts": _health_job_counts(),
        },
    )


@login_required
def confirm_delete_job(request, job_id):
    """GET — show a confirmation page before soft-deleting a job."""
    job = get_object_or_404(
        TranslationJob.objects.filter(is_deleted=False),
        id=job_id,
        owner=request.user,
    )
    state = _job_card_context(job)
    return render(request, "translator/confirm_delete.html", state)


@login_required
@require_POST
def delete_job(request, job_id):
    """POST — soft-delete a job owned by the current user.

    Only the job owner may delete their own job.  Superusers use the admin
    interface for administrative deletions.
    """
    from django.utils import timezone

    job = get_object_or_404(
        TranslationJob.objects.filter(is_deleted=False),
        id=job_id,
        owner=request.user,
    )

    job.is_deleted = True
    job.deleted_at = timezone.now()
    job.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    UserActivityLog.objects.create(
        user=request.user,
        action="delete_job",
        object_type="TranslationJob",
        object_id=job.job_id,
        metadata={"filename": job.original_filename, "status": job.status},
    )

    SystemEventLog.objects.create(
        actor=request.user,
        job=job,
        event_type="translation_job_deleted",
        message=f"Job soft-deleted by owner: {job.original_filename}",
        metadata={"filename": job.original_filename, "status": job.status},
    )

    messages.success(request, f"‘{job.original_filename}’ has been removed from your history.")
    return redirect("translator:history")


def _health_checks() -> list:
    dataset_loaded = translation_dataset_loaded()
    return [
        _database_health(),
        _task_mode_health(),
        _redis_health(),
        _celery_worker_health(),
        _tesseract_health(),
        _ocr_languages_health(),
        _media_writability_health(),
        {
            "name": "Translation dataset",
            "status": "ok" if dataset_loaded else "warning",
            "detail": "Loaded" if dataset_loaded else "Not loaded",
        },
        _neural_model_health(),
    ]


def _neural_model_health() -> dict:
    """Report ByT5 neural fallback status without exposing the model path."""
    try:
        from translator.services.neural_translation_service import (
            get_neural_translation_service,
        )

        service = get_neural_translation_service()
        if not service.is_enabled():
            return {
                "name": "Neural fallback (ByT5)",
                "status": "warning",
                "detail": "Disabled (experimental; phrasebook handles all segments)",
            }
        if service.is_available():
            return {
                "name": "Neural fallback (ByT5)",
                "status": "ok",
                "detail": "Enabled and loaded (experimental; all output needs review)",
            }
        return {
            "name": "Neural fallback (ByT5)",
            "status": "warning",
            "detail": service.load_warning or "Enabled but model not loaded",
        }
    except Exception as exc:
        return {"name": "Neural fallback (ByT5)", "status": "error", "detail": str(exc)}


def _database_health() -> dict:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"name": "Database", "status": "ok", "detail": connection.vendor}
    except Exception as exc:
        return {"name": "Database", "status": "error", "detail": str(exc)}


def _task_mode_health() -> dict:
    task_mode = getattr(settings, "LINGOKATUTUBO_TASK_MODE", "thread")
    status = "ok" if str(task_mode).lower() == "celery" else "warning"
    detail = (
        "Celery background worker mode"
        if status == "ok"
        else "Development runner; use Celery for durable background processing"
    )
    return {"name": "Task mode", "status": status, "detail": f"{task_mode} - {detail}"}


def _redis_health() -> dict:
    broker_url = getattr(settings, "CELERY_BROKER_URL", "")
    safe_broker = _safe_service_url(broker_url)
    try:
        import redis

        client = redis.Redis.from_url(
            broker_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
        return {"name": "Redis / broker", "status": "ok", "detail": safe_broker}
    except Exception as exc:
        return {
            "name": "Redis / broker",
            "status": "error",
            "detail": f"{safe_broker} unavailable: {exc}",
        }


def _celery_worker_health() -> dict:
    try:
        from lingokatutubo_django.celery import app as celery_app

        replies = celery_app.control.ping(timeout=1)
        if replies:
            return {
                "name": "Celery worker",
                "status": "ok",
                "detail": f"{len(replies)} worker(s) responded",
            }
        return {
            "name": "Celery worker",
            "status": "warning",
            "detail": "No workers responded",
        }
    except Exception as exc:
        return {"name": "Celery worker", "status": "error", "detail": str(exc)}


def _tesseract_health() -> dict:
    try:
        from translator.services.ocr_stage import get_ocr_service

        service = get_ocr_service()
        if service.is_available():
            return {"name": "Tesseract", "status": "ok", "detail": "Available"}
        return {"name": "Tesseract", "status": "error", "detail": "Unavailable"}
    except Exception as exc:
        return {"name": "Tesseract", "status": "error", "detail": str(exc)}


def _ocr_languages_health() -> dict:
    try:
        from translator.services.ocr_stage import get_ocr_service

        languages = get_ocr_service().get_installed_languages()
        detail = ", ".join(languages) if languages else "No languages reported"
        return {
            "name": "Installed OCR languages",
            "status": "ok" if languages else "warning",
            "detail": detail,
        }
    except Exception as exc:
        return {
            "name": "Installed OCR languages",
            "status": "error",
            "detail": str(exc),
        }


def _media_writability_health() -> dict:
    media_root = Path(settings.MEDIA_ROOT)
    probe_dir = media_root / "jobs" / ".health"
    probe_file = probe_dir / "write-check.tmp"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
        return {
            "name": "Media/job directory",
            "status": "ok",
            "detail": f"Writable: {media_root}",
        }
    except Exception as exc:
        return {
            "name": "Media/job directory",
            "status": "error",
            "detail": f"Not writable: {exc}",
        }


def _health_job_counts() -> dict:
    cutoff = timezone.now() - timezone.timedelta(minutes=15)
    active_filter = Q(is_deleted=False)
    return {
        "queued": TranslationJob.objects.filter(
            active_filter,
            status=TranslationJob.Status.QUEUED,
        ).count(),
        "processing": TranslationJob.objects.filter(
            active_filter,
            status=TranslationJob.Status.PROCESSING,
        ).count(),
        "retrying": TranslationJob.objects.filter(
            active_filter,
            status=TranslationJob.Status.RETRYING,
        ).count(),
        "stale": TranslationJob.objects.filter(
            active_filter,
            status__in=[
                TranslationJob.Status.QUEUED,
                TranslationJob.Status.PROCESSING,
                TranslationJob.Status.RETRYING,
            ],
            updated_at__lte=cutoff,
        ).count(),
    }


def _safe_service_url(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return "configured"
    if not parsed.scheme:
        return "configured"
    host = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    return f"{parsed.scheme}://{host}{port}{path}"


def _get_owned_job(request, job_id) -> TranslationJob:
    return get_object_or_404(_owned_jobs_queryset(request), id=job_id)


def _owned_jobs_queryset(request):
    queryset = TranslationJob.objects.filter(is_deleted=False)

    if not request.user.is_superuser:
        queryset = queryset.filter(owner=request.user)

    return queryset


def _job_payload(job: TranslationJob) -> dict:
    metadata = job.metadata or {}
    state = _job_state_context(job)
    translation_quality = _translation_quality_context(job)

    return {
        "job_id": job.job_id,
        "status": job.status,
        "message": job.phase_message or _status_message(job.status),
        "output_file": job.output_file_path or None,
        "progress": job.progress,
        "progress_percent": 100
        if job.status == TranslationJob.Status.COMPLETED
        else job.progress,
        "current_phase": job.current_phase,
        "current_step": job.current_step,
        "phase_message": job.phase_message or _status_message(job.status),
        "detection_type": job.detection_type or None,
        "file_type": job.file_type,
        "error": _failure_message(job) if job.status == TranslationJob.Status.FAILED else None,
        "detected_language": metadata.get("detected_language"),
        "detection_confidence": metadata.get("detection_confidence"),
        "is_mixed_language": metadata.get("is_mixed_language", False),
        "metadata": metadata,
        "can_preview": state["can_preview"],
        "can_download": state["can_download"],
        "is_processing": state["is_processing"],
        "has_failed": state["has_failed"],
        **translation_quality,
        "created_at": job.created_at.isoformat(),
        "started_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "is_retrying": job.status == TranslationJob.Status.RETRYING,
        "is_taking_longer": _job_is_taking_longer(job),
    }


def _status_message(status: str) -> str:
    return {
        TranslationJob.Status.QUEUED: "Job queued",
        TranslationJob.Status.RETRYING: "Job is being retried",
        TranslationJob.Status.PROCESSING: "Translation in progress",
        TranslationJob.Status.COMPLETED: "Translation complete",
        TranslationJob.Status.FAILED: "Translation failed",
    }.get(status, status)


def _preview_image_url(job: TranslationJob, path_or_name: str) -> str:
    filename = Path(str(path_or_name).replace("\\", "/")).name

    if not filename:
        return ""

    return reverse(
        "translator:translate_preview_image",
        kwargs={"job_id": job.job_id, "image_name": filename},
    )


def _default_structure_path(job: TranslationJob) -> str:
    return job_structure_path(job.job_id)


def _resolve_job_file(job: TranslationJob, path: str):
    if not path:
        return None

    job_dir = Path(job_directory_path(job.job_id)).resolve()
    candidate = Path(path).resolve()

    try:
        common_path = os.path.commonpath([str(job_dir), str(candidate)])
    except ValueError:
        return None

    if common_path != str(job_dir):
        return None

    return candidate


def _job_state_context(job: TranslationJob) -> dict:
    is_processing = job.status in {
        TranslationJob.Status.QUEUED,
        TranslationJob.Status.RETRYING,
        TranslationJob.Status.PROCESSING,
    }

    has_failed = job.status == TranslationJob.Status.FAILED
    can_preview = job.status == TranslationJob.Status.COMPLETED
    can_download = can_preview and _translated_output_exists(job)

    # Phase 4: expose OCR/extraction metadata from job.metadata JSONField
    metadata = job.metadata or {}
    raw_extraction_method = metadata.get("extraction_method", "")
    extraction_method_label = _extraction_method_label(raw_extraction_method)

    ocr_summary = metadata.get("ocr_summary") or {}
    mean_confidence = ocr_summary.get("mean_confidence")  # 0.0–1.0 or None
    ocr_confidence_pct = round(mean_confidence * 100) if mean_confidence is not None else None
    ocr_has_low_quality = bool(ocr_summary.get("has_low_quality_warning", False))

    # Gather OCR warnings from job metadata (set by pipeline) and from OCRResults
    ocr_warnings: list = list(metadata.get("ocr_warnings") or [])
    try:
        for ocr_result in job.ocr_results.all():
            for w in (ocr_result.warnings or []):
                if w and w not in ocr_warnings:
                    ocr_warnings.append(w)
    except Exception:
        pass

    return {
        "job": job,
        "can_preview": can_preview,
        "can_download": can_download,
        "is_processing": is_processing,
        "has_failed": has_failed,
        "can_retry": False,
        # Phase 4 fields
        "extraction_method": raw_extraction_method,
        "extraction_method_label": extraction_method_label,
        "ocr_confidence_pct": ocr_confidence_pct,
        "ocr_has_low_quality": ocr_has_low_quality,
        "ocr_warnings": ocr_warnings,
    }


def _job_card_context(job: TranslationJob) -> dict:
    context = _job_state_context(job)
    uploaded_document = _uploaded_document_for(job)
    ocr_summary = _ocr_summary(job)
    thumbnail = _thumbnail_context(job)
    translation_quality = _translation_quality_context(job)

    context.update(
        {
            "uploaded_document": uploaded_document,
            "document_title": (
                getattr(uploaded_document, "original_filename", "")
                or job.original_filename
            ),
            "uploaded_at": getattr(uploaded_document, "created_at", None)
            or job.created_at,
            "completed_at": job.completed_at,
            "ocr_confidence": _annotated_or_summary(
                job,
                "ocr_confidence",
                ocr_summary["confidence"],
            ),
            **translation_quality,
            "workflow_steps": _workflow_steps(job),
            "page_count": int(
                getattr(job, "page_count", 0)
                or job.pages.count()
                or _metadata_document_page_count(job.metadata or {})
            ),
            "thumbnail_url": thumbnail["url"],
            "thumbnail_kind": thumbnail["kind"],
            "thumbnail_label": thumbnail["label"],
            "file_icon_class": _file_icon_class(job.file_type),
            "status_label": _status_label(job.status),
            "status_class": _status_class(job.status),
            "failure_message": _failure_message(job),
            "technical_error_detail": (
                clean_invisible_unicode(job.error)
                if job.status == TranslationJob.Status.FAILED and job.error
                else ""
            ),
            "progress_percent": 100
            if job.status == TranslationJob.Status.COMPLETED
            else job.progress,
            "is_retrying": job.status == TranslationJob.Status.RETRYING,
            "is_taking_longer": _job_is_taking_longer(job),
            "status_api_url": reverse("translator:status", args=[job.id]),
        }
    )

    return context


def _positive_int(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _metadata_document_page_count(metadata: dict) -> int:
    return max(
        _positive_int(metadata.get("translated_page_count")),
        _positive_int(metadata.get("source_page_count")),
        _positive_int(metadata.get("layout_page_count")),
        _positive_int(metadata.get("page_count")),
    )


def _translation_quality_context(job: TranslationJob) -> dict:
    segment_count = _segment_count(job)
    review_segment_count = _review_segment_count(job)
    translation_confidence = _translation_confidence(job)

    return {
        "segment_count": segment_count,
        "review_segment_count": review_segment_count,
        "translation_confidence": translation_confidence,
        "translation_confidence_pct": _confidence_pct(translation_confidence),
        "translation_has_review_items": review_segment_count > 0,
    }


def _segment_count(job: TranslationJob) -> int:
    return int(getattr(job, "segment_count", 0) or job.segments.count())


def _review_segment_count(job: TranslationJob) -> int:
    annotated = getattr(job, "review_segment_count", None)
    if annotated is not None:
        return int(annotated or 0)
    return int(job.segments.filter(needs_review=True).count())


def _translation_confidence(job: TranslationJob):
    annotated = getattr(job, "translation_confidence", None)
    if annotated is not None:
        return annotated

    aggregate = job.segments.aggregate(value=Avg("confidence"))
    return aggregate["value"]


def _confidence_pct(confidence):
    return round(confidence * 100) if confidence is not None else None


def _workflow_steps(job: TranslationJob) -> list:
    steps = [
        ("uploaded", "Uploaded"),
        ("extracting", "Extracting / OCR"),
        ("translating", "Translating"),
        ("output", "Generating Output"),
        ("completed", "Completed"),
    ]

    if job.status == TranslationJob.Status.FAILED:
        steps[-1] = ("failed", "Failed")
        completed_index = _workflow_phase_index(job.current_phase)
        active_index = None
    elif job.status == TranslationJob.Status.COMPLETED:
        completed_index = len(steps) - 1
        active_index = None
    elif job.status in {
        TranslationJob.Status.QUEUED,
        TranslationJob.Status.RETRYING,
        TranslationJob.Status.PROCESSING,
    }:
        active_index = _workflow_phase_index(job.current_phase)
        completed_index = max(0, active_index - 1)
    else:
        completed_index = 0
        active_index = None

    output = []

    for index, (key, label) in enumerate(steps):
        if job.status == TranslationJob.Status.FAILED and index == len(steps) - 1:
            state = "failed"
        elif active_index == index:
            state = "active"
        elif index <= completed_index:
            state = "complete"
        else:
            state = "pending"

        output.append({"key": key, "label": label, "state": state})

    return output


def _workflow_phase_index(phase: str) -> int:
    phase = str(phase or "").lower()
    phase_map = {
        "queued": 0,
        "retrying": 0,
        "uploading": 0,
        "detecting": 1,
        "extracting": 1,
        "ocr": 1,
        "translating": 2,
        "reconstructing": 3,
        "preview_generation": 3,
        "bilingual_output": 3,
        "completed": 4,
        "failed": 4,
    }
    return phase_map.get(phase, 1)


def _job_is_taking_longer(job: TranslationJob) -> bool:
    if job.status not in {
        TranslationJob.Status.QUEUED,
        TranslationJob.Status.PROCESSING,
        TranslationJob.Status.RETRYING,
    }:
        return False
    threshold_minutes = int(
        getattr(settings, "LINGOKATUTUBO_LONG_JOB_MINUTES", 10)
    )
    return job.created_at <= timezone.now() - timezone.timedelta(
        minutes=threshold_minutes
    )


def _uploaded_document_for(job: TranslationJob):
    try:
        return job.uploaded_document
    except UploadedDocument.DoesNotExist:
        return None


def _annotated_or_summary(job: TranslationJob, name: str, fallback):
    value = getattr(job, name, None)
    return fallback if value is None else value


def _thumbnail_context(job: TranslationJob) -> dict:
    for page in _iter_related(job, "pages"):
        url = _safe_preview_url(job, getattr(page, "source_image_path", ""))

        if url:
            return {
                "url": url,
                "kind": "image",
                "label": f"Page {page.page_number}",
            }

    metadata = job.metadata or {}

    for item in metadata.get("preview_original", []):
        url = _safe_preview_url(job, item)

        if url:
            return {
                "url": url,
                "kind": "image",
                "label": "Preview",
            }

    for output in _iter_related(job, "generated_outputs"):
        if output.output_type != GeneratedOutput.OutputType.PREVIEW_IMAGE:
            continue

        url = _safe_preview_url(job, output.file_path)

        if url:
            return {
                "url": url,
                "kind": "image",
                "label": "Preview",
            }

    return {
        "url": "",
        "kind": "icon",
        "label": _file_type_label(job.file_type),
    }


def _safe_preview_url(job: TranslationJob, path_or_name: str) -> str:
    if not path_or_name:
        return ""

    filename = Path(str(path_or_name).replace("\\", "/")).name

    if not filename or not PREVIEW_IMAGE_NAME_RE.fullmatch(filename):
        return ""

    return _preview_image_url(job, filename)


def _iter_related(job: TranslationJob, related_name: str):
    manager = getattr(job, related_name)

    try:
        return list(manager.all())
    except Exception:
        return []


def _file_type_label(file_type: str) -> str:
    return {
        "pdf": "PDF",
        "docx": "DOCX",
        "jpg": "IMG",
        "png": "IMG",
        "txt": "TXT",
    }.get(str(file_type).lower(), "DOC")


def _file_icon_class(file_type: str) -> str:
    return f"file-{str(file_type or 'document').lower()}"


def _extraction_method_label(method: str) -> str:
    """Convert internal extraction_method code to a human-readable label."""
    return {
        'direct_pdf_text': 'Direct PDF Text (PyMuPDF)',
        'ocr_image': 'OCR - Tesseract',
        'docx_text': 'DOCX Text (python-docx)',
        'plain_text': 'Plain Text',
        'hybrid': 'Hybrid (text + OCR)',
        'text_extraction': 'Text Extraction',
    }.get(method, method.replace('_', ' ').title() if method else '—')


def _status_label(status: str) -> str:
    return {
        TranslationJob.Status.QUEUED: 'Pending',
        TranslationJob.Status.RETRYING: 'Retrying',
        TranslationJob.Status.PROCESSING: 'Processing',
        TranslationJob.Status.COMPLETED: 'Completed',
        TranslationJob.Status.FAILED: 'Failed',
    }.get(status, status.title() if status else 'Unknown')


def _status_class(status: str) -> str:
    if status == TranslationJob.Status.QUEUED:
        return 'pending'
    if status == TranslationJob.Status.RETRYING:
        return 'retrying'
    return str(status or 'unknown')


def _failure_message(job: TranslationJob) -> str:
    """User-facing failure copy — always the safe generic message.

    The raw backend error (``job.error``) may contain codec errors,
    tracebacks, or file paths; it is exposed separately, only to staff, via
    ``_job_card_context``'s ``technical_error_detail``.
    """
    if job.status != TranslationJob.Status.FAILED:
        return ''
    return GENERIC_TRANSLATION_FAILURE_MESSAGE


def _translated_output_exists(job: TranslationJob) -> bool:
    return _translated_output_path(job) is not None


def _translated_output_path(job: TranslationJob):
    resolved = _resolve_job_file(job, job.output_file_path)

    if resolved and resolved.exists():
        return resolved

    for output in job.generated_outputs.filter(
        output_type=GeneratedOutput.OutputType.TRANSLATED_PDF
    ):
        output_path = _resolve_job_file(job, output.file_path)

        if output_path and output_path.exists():
            return output_path

    return None


def _ocr_summary(job: TranslationJob) -> dict:
    results = list(job.ocr_results.all())
    confidences = [
        result.confidence for result in results if result.confidence is not None
    ]

    return {
        "count": len(results),
        "confidence": round(sum(confidences) / len(confidences), 4)
        if confidences
        else None,
        "warnings": [
            warning for result in results for warning in (result.warnings or [])
        ],
    }


def _image_preview_context(job: TranslationJob) -> dict:
    metadata = job.metadata or {}

    original_pages = [
        _preview_image_url(job, item)
        for item in metadata.get("preview_original", [])
        if _preview_image_url(job, item)
    ]

    translated_pages = [
        _preview_image_url(job, item)
        for item in metadata.get("preview_translated", [])
        if _preview_image_url(job, item)
    ]
    declared_page_count = _metadata_document_page_count(metadata)

    document_pages = list(_iter_related(job, "pages"))
    page_text = {
        page.page_number: clean_invisible_unicode(page.extracted_text or "").strip()
        for page in document_pages
    }
    source_segments = {}
    translated_segments = {}
    page_numbers = set(page_text)

    for segment in job.segments.select_related("page").order_by("segment_index"):
        # Segments are normally linked to the DocumentPage they were
        # extracted from. Fall back to page 1 so a segment without that
        # link (e.g. legacy data) still surfaces in the normal-user-facing
        # Original/Translated panels instead of disappearing.
        page_number = segment.page.page_number if segment.page else 1
        page_numbers.add(page_number)
        if segment.source_text:
            source_segments.setdefault(page_number, []).append(
                clean_invisible_unicode(segment.source_text)
            )
        display_translation = get_display_translation(segment)
        if display_translation:
            translated_segments.setdefault(page_number, []).append(display_translation)

    image_page_count = max(len(original_pages), len(translated_pages))
    if image_page_count:
        page_numbers.update(range(1, image_page_count + 1))
    if declared_page_count:
        page_numbers.update(range(1, declared_page_count + 1))

    page_pairs = []
    for page_number in sorted(page_numbers):
        original_text = page_text.get(page_number) or "\n".join(
            source_segments.get(page_number, [])
        )
        translated_text = "\n".join(translated_segments.get(page_number, []))
        page_pairs.append(
            {
                "number": page_number,
                "original_url": original_pages[page_number - 1]
                if page_number - 1 < len(original_pages)
                else "",
                "translated_url": translated_pages[page_number - 1]
                if page_number - 1 < len(translated_pages)
                else "",
                "original_text": original_text,
                "translated_text": translated_text,
            }
        )

    return {
        "original_pages": original_pages,
        "translated_pages": translated_pages,
        "page_pairs": page_pairs,
        "page_count": max(
            len(original_pages),
            len(translated_pages),
            len(page_pairs),
            declared_page_count,
        ),
        "document_page_count": max(len(page_pairs), declared_page_count),
        "previewed_page_count": image_page_count,
    }


def _display_safe_bilingual_first_page(data: dict) -> dict:
    safe = copy.deepcopy(data or {"blocks": []})
    for block in safe.get("blocks", []):
        source_text = clean_invisible_unicode(
            block.get("source_text") or block.get("original_text") or ""
        )
        method = block.get("translation_method") or block.get("method")
        cascade_stage = block.get("cascade_stage")
        translated_text = block.get("display_translated_text")
        if translated_text is None:
            translated_text = block.get("translated_text")
        display_text = get_display_translation_text(
            source_text,
            translated_text,
            method,
            cascade_stage,
        )
        block["display_translated_text"] = display_text
        block["translated_text"] = display_text
        block.pop("raw_translated_text", None)
    return safe


def _display_safe_structure(data: dict) -> dict:
    safe = copy.deepcopy(data)
    for page in safe.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("type") != "text" and block.get("block_type") != "text":
                continue
            block_source_parts = []
            block_display_parts = []
            for line in block.get("lines", []):
                source_text = clean_invisible_unicode(
                    line.get("source_text") or line.get("text") or ""
                ).strip()
                method = line.get("translation_method") or line.get("cascade_stage")
                cascade_stage = line.get("cascade_stage")
                translated_text = line.get("display_translated_text")
                if translated_text is None:
                    translated_text = line.get("translated_text")
                display_text = get_display_translation_text(
                    source_text,
                    translated_text,
                    method,
                    cascade_stage,
                )
                line["display_translated_text"] = display_text
                line["translated_text"] = display_text
                if source_text:
                    block_source_parts.append(source_text)
                if display_text:
                    block_display_parts.append(display_text)

            block_source = clean_invisible_unicode(
                block.get("source_text") or block.get("original_text") or " ".join(block_source_parts)
            )
            block_method = block.get("translation_method") or block.get("cascade_stage")
            block_display = block.get("display_translated_text")
            if block_display is None:
                block_display = block.get("translated_text")
            display_text = get_display_translation_text(
                block_source,
                block_display or " ".join(block_display_parts),
                block_method,
                block.get("cascade_stage"),
            )
            block["display_translated_text"] = display_text
            block["translated_text"] = display_text
    return safe


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()
