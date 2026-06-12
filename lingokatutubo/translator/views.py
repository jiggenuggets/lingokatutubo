import hashlib
import json
import os
import re
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Prefetch, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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


PREVIEW_IMAGE_NAME_RE = re.compile(r"^(?:original|translated)_page_\d+\.png$")


def home(request):
    return render(request, "translator/home.html")


def about(request):
    return render(request, "translator/about.html")


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


@login_required
def translate(request):
    recent_query = (
        _owned_jobs_queryset(request)
        .select_related("uploaded_document")
        .annotate(
            segment_count=Count("segments", distinct=True),
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
            in {TranslationJob.Status.QUEUED, TranslationJob.Status.PROCESSING}
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
            ocr_confidence=Avg("ocr_results__confidence"),
            page_count=Count("pages", distinct=True),
            output_count=Count("generated_outputs", distinct=True),
        )
        .prefetch_related("generated_outputs", "pages", "ocr_results")
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
            ocr_confidence=Avg("ocr_results__confidence"),
            page_count=Count("pages", distinct=True),
        )
        .prefetch_related("generated_outputs", "pages", "ocr_results")
        .filter(id=job_id)
        .first()
    )

    if not job:
        raise Http404("Job not found")

    return render(request, "translator/job_detail.html", _job_card_context(job))


@login_required
def preview(request, job_id):
    job = (
        _owned_jobs_queryset(request)
        .prefetch_related(
            Prefetch(
                "segments",
                queryset=TranslationSegment.objects.select_related("page").order_by(
                    "segment_index"
                ),
            ),
            "ocr_results",
            "generated_outputs",
            "pages",
        )
        .select_related("uploaded_document")
        .annotate(
            segment_count=Count("segments", distinct=True),
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

    return render(
        request,
        "translator/preview.html",
        {
            **state,
            "uploaded_document": getattr(job, "uploaded_document", None),
            "document_pages": list(job.pages.all()),
            "ocr_result": job.ocr_results.first(),
            "segments": list(job.segments.all()),
            "generated_outputs": list(job.generated_outputs.all()),
            "ocr_summary": _ocr_summary(job),
            "image_preview": _image_preview_context(job),
            "thumbnail_url": state.get("thumbnail_url", ""),
            "status_label": state.get("status_label", _status_label(job.status)),
            "status_class": state.get("status_class", _status_class(job.status)),
        },
    )


@login_required
@require_POST
def api_translate(request):
    form = DocumentUploadForm(request.POST, request.FILES)

    if not form.is_valid():
        return JsonResponse({"detail": form.errors.get_json_data()}, status=400)

    uploaded = form.cleaned_data["file"]
    file_type = detect_upload_file_type(uploaded.name)

    if not file_type:
        return JsonResponse({"detail": "Unsupported file type."}, status=400)

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


@login_required
def api_job_status(request, job_id):
    job = _get_owned_job(request, job_id)
    return JsonResponse(_job_payload(job))


@login_required
def api_structure(request, job_id):
    job = _get_owned_job(request, job_id)

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

    return JsonResponse(data)


@login_required
def api_preview(request, job_id):
    job = _get_owned_job(request, job_id)

    if job.status != TranslationJob.Status.COMPLETED:
        return JsonResponse(
            {"error": f"Job status is {job.status}, not completed"},
            status=400,
        )

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

    return JsonResponse(
        {
            "job_id": job.job_id,
            "left_page_preview": original_pages[0] if original_pages else None,
            "bilingual_first_page": metadata.get("bilingual_first_page", {"blocks": []}),
            "original_pages": original_pages,
            "translated_pages": translated_pages,
            "page_count": max(len(original_pages), len(translated_pages)),
            "segments": [
                {
                    "segment_index": segment.segment_index,
                    "source_text": segment.source_text,
                    "translated_text": segment.translated_text,
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


def health(request):
    return JsonResponse(
        {
            "status": "ok",
            "translation_dataset_loaded": translation_dataset_loaded(),
            "database": "configured",
        }
    )


def _get_owned_job(request, job_id) -> TranslationJob:
    return get_object_or_404(_owned_jobs_queryset(request), id=job_id)


def _owned_jobs_queryset(request):
    queryset = TranslationJob.objects.all()

    if not request.user.is_superuser:
        queryset = queryset.filter(owner=request.user)

    return queryset


def _job_payload(job: TranslationJob) -> dict:
    metadata = job.metadata or {}
    state = _job_state_context(job)

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
        "error": job.error or _failure_message(job) if job.status == TranslationJob.Status.FAILED else None,
        "detected_language": metadata.get("detected_language"),
        "detection_confidence": metadata.get("detection_confidence"),
        "is_mixed_language": metadata.get("is_mixed_language", False),
        "metadata": metadata,
        "can_preview": state["can_preview"],
        "can_download": state["can_download"],
        "is_processing": state["is_processing"],
        "has_failed": state["has_failed"],
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _status_message(status: str) -> str:
    return {
        TranslationJob.Status.QUEUED: "Job queued",
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
        TranslationJob.Status.PROCESSING,
    }

    has_failed = job.status == TranslationJob.Status.FAILED
    can_preview = job.status == TranslationJob.Status.COMPLETED
    can_download = can_preview and _translated_output_exists(job)

    return {
        "job": job,
        "can_preview": can_preview,
        "can_download": can_download,
        "is_processing": is_processing,
        "has_failed": has_failed,
        "can_retry": False,
    }


def _job_card_context(job: TranslationJob) -> dict:
    context = _job_state_context(job)
    uploaded_document = _uploaded_document_for(job)
    ocr_summary = _ocr_summary(job)
    thumbnail = _thumbnail_context(job)

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
            "segment_count": int(
                getattr(job, "segment_count", 0) or job.segments.count()
            ),
            "page_count": int(getattr(job, "page_count", 0) or job.pages.count()),
            "thumbnail_url": thumbnail["url"],
            "thumbnail_kind": thumbnail["kind"],
            "thumbnail_label": thumbnail["label"],
            "file_icon_class": _file_icon_class(job.file_type),
            "status_label": _status_label(job.status),
            "status_class": _status_class(job.status),
            "failure_message": _failure_message(job),
        }
    )

    return context


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


def _status_label(status: str) -> str:
    return {
        TranslationJob.Status.QUEUED: "Pending",
        TranslationJob.Status.PROCESSING: "Processing",
        TranslationJob.Status.COMPLETED: "Completed",
        TranslationJob.Status.FAILED: "Failed",
    }.get(status, status.title() if status else "Unknown")


def _status_class(status: str) -> str:
    if status == TranslationJob.Status.QUEUED:
        return "pending"

    return str(status or "unknown")


def _failure_message(job: TranslationJob) -> str:
    if job.status != TranslationJob.Status.FAILED:
        return ""

    return job.error or "Translation failed safely"


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

    return {
        "original_pages": original_pages,
        "translated_pages": translated_pages,
        "page_count": max(len(original_pages), len(translated_pages)),
    }


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()