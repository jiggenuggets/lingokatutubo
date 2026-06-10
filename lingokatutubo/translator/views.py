import json
import os
import re
from pathlib import Path

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .forms import DocumentUploadForm, SignUpForm
from .models import TranslationJob
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
    jobs = TranslationJob.objects.filter(owner=request.user)[:8]
    return render(
        request,
        "translator/translate.html",
        {
            "upload_form": DocumentUploadForm(),
            "jobs": jobs,
        },
    )


@login_required
def preview(request, job_id):
    job = _get_owned_job(request, job_id)
    return render(request, "translator/preview.html", {"job": job})


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
    structure_path = _resolve_job_file(job, job.structure_file_path or _default_structure_path(job))
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
    if job.status != TranslationJob.Status.COMPLETED:
        return JsonResponse(
            {"error": f"Job not completed yet: {job.status}", "detail": job.error},
            status=400,
        )

    requested_format = request.GET.get("format", "translated")
    path = job.bilingual_file_path if requested_format == "bilingual" else job.output_file_path
    resolved_path = _resolve_job_file(job, path)
    if not resolved_path or not resolved_path.exists():
        return JsonResponse({"error": "Translated PDF not found"}, status=404)

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
    source_language = request.POST.get("source_language") or request.GET.get("source_language") or "auto"
    target_language = request.POST.get("target_language") or request.GET.get("target_language") or "tagabawa"
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
    queryset = TranslationJob.objects.all()
    if not request.user.is_superuser:
        queryset = queryset.filter(owner=request.user)
    return get_object_or_404(queryset, id=job_id)


def _job_payload(job: TranslationJob) -> dict:
    metadata = job.metadata or {}
    return {
        "job_id": job.job_id,
        "status": job.status,
        "message": job.phase_message or _status_message(job.status),
        "output_file": job.output_file_path or None,
        "progress": job.progress,
        "progress_percent": 100 if job.status == TranslationJob.Status.COMPLETED else job.progress,
        "current_phase": job.current_phase,
        "current_step": job.current_step,
        "phase_message": job.phase_message or _status_message(job.status),
        "detection_type": job.detection_type or None,
        "file_type": job.file_type,
        "error": job.error or None,
        "detected_language": metadata.get("detected_language"),
        "detection_confidence": metadata.get("detection_confidence"),
        "is_mixed_language": metadata.get("is_mixed_language", False),
        "metadata": metadata,
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
