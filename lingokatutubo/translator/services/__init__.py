import asyncio
import json
import os
from typing import Any, Dict, Optional

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from translator.models import (
    DocumentPage,
    GeneratedOutput,
    OCRResult,
    SystemEventLog,
    TranslationJob,
    TranslationSegment,
)

from . import file_service as file_service_module
from .models import FileType
from .pipeline_service import get_pipeline_service
from .task_runner import submit_translation_task
from .translation_dataset import get_translation_dataset

_CALLBACK_REGISTERED = False


def _get_file_service():
    current = getattr(file_service_module, "_file_service", None)
    media_root = str(settings.MEDIA_ROOT)
    if (
        current is None
        or getattr(current, "storage_layout", "") != "django"
        or str(getattr(current, "upload_dir", "")) != media_root
    ):
        file_service_module._file_service = file_service_module.FileService(
            upload_dir=media_root,
            storage_layout="django",
        )
    return file_service_module._file_service


def _get_file_type_enum():
    return FileType


def _get_pipeline_service():
    return get_pipeline_service()


def _get_translation_dataset():
    return get_translation_dataset()


def _enum_value(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def detect_upload_file_type(filename: str) -> Optional[str]:
    file_type = _get_file_service().get_file_type(filename)
    return _enum_value(file_type) if file_type else None


def save_uploaded_file(uploaded_file, job_id: str) -> Dict[str, str]:
    file_service = _get_file_service()
    content = b"".join(uploaded_file.chunks())
    input_path = asyncio.run(
        file_service.save_upload(content, uploaded_file.name, job_id)
    )
    return {
        "input_file_path": input_path,
        "upload_file_path": file_service.get_upload_archive_path(job_id, uploaded_file.name),
    }


def _job_paths(job_id: str, metadata: Dict[str, Any]) -> Dict[str, str]:
    file_service = _get_file_service()
    translated = file_service.get_output_path(job_id, "translated.pdf")
    bilingual = metadata.get("bilingual_pdf") or file_service.get_output_path(job_id, "bilingual.pdf")
    structure = metadata.get("structure_file") or os.path.join(
        file_service.get_job_dir(job_id),
        "structure.json",
    )
    return {
        "output_file_path": translated if os.path.exists(translated) else "",
        "bilingual_file_path": bilingual if bilingual and os.path.exists(str(bilingual)) else "",
        "structure_file_path": structure if structure and os.path.exists(str(structure)) else "",
    }


def sync_pipeline_job(pipeline_job) -> None:
    close_old_connections()
    job_id = str(pipeline_job.job_id)
    metadata = dict(getattr(pipeline_job, "metadata", {}) or {})
    paths = _job_paths(job_id, metadata)
    completed_at = getattr(pipeline_job, "completed_at", None)

    updates = {
        "status": str(getattr(pipeline_job, "status", "queued") or "queued"),
        "progress": int(getattr(pipeline_job, "progress", 0) or 0),
        "current_phase": str(getattr(pipeline_job, "current_phase", "") or ""),
        "current_step": str(getattr(pipeline_job, "current_step", "") or ""),
        "phase_message": str(getattr(pipeline_job, "phase_message", "") or ""),
        "error": str(getattr(pipeline_job, "error", "") or ""),
        "detection_type": _enum_value(getattr(pipeline_job, "detection_type", None)),
        "metadata": metadata,
        "updated_at": timezone.now(),
        **paths,
    }
    if getattr(pipeline_job, "file_type", None):
        updates["file_type"] = _enum_value(pipeline_job.file_type)
    if completed_at:
        updates["completed_at"] = timezone.make_aware(completed_at) if timezone.is_naive(completed_at) else completed_at

    TranslationJob.objects.filter(id=job_id).update(**updates)
    if updates["status"] == TranslationJob.Status.FAILED:
        _log_failed_job(job_id, updates["error"] or updates["phase_message"])
    _sync_generated_outputs(job_id, paths)
    if paths.get("structure_file_path"):
        _sync_structure_models(job_id, paths["structure_file_path"])


def register_pipeline_callback() -> None:
    global _CALLBACK_REGISTERED
    if _CALLBACK_REGISTERED:
        return
    _get_pipeline_service().set_job_update_callback(sync_pipeline_job)
    _CALLBACK_REGISTERED = True


def start_translation_job(job: TranslationJob) -> None:
    register_pipeline_callback()
    file_type = _get_file_type_enum()(job.file_type)
    submit_translation_task(
        _run_translation_job,
        job.job_id,
        job.input_file_path,
        file_type.value,
        job.source_language,
        job.target_language,
        job.ocr_languages or None,
    )


def _run_translation_job(
    job_id: str,
    input_file_path: str,
    file_type: Any,
    source_language: str,
    target_language: str,
    ocr_languages: Optional[str],
) -> None:
    close_old_connections()
    try:
        register_pipeline_callback()
        file_type_enum = (
            file_type
            if isinstance(file_type, _get_file_type_enum())
            else _get_file_type_enum()(file_type)
        )
        asyncio.run(
            _get_pipeline_service().process_translation(
                job_id=job_id,
                input_file_path=input_file_path,
                file_type=file_type_enum,
                source_language=source_language,
                target_language=target_language,
                ocr_languages=ocr_languages,
            )
        )
    except Exception as exc:
        TranslationJob.objects.filter(id=job_id).update(
            status=TranslationJob.Status.FAILED,
            error=str(exc),
            phase_message=str(exc),
            current_step="Translation failed",
            updated_at=timezone.now(),
        )
        _log_failed_job(job_id, str(exc))
    finally:
        close_old_connections()


def quick_translate_text(
    text: str,
    source_language: str = "auto",
    target_language: str = "tagabawa",
) -> Dict[str, Any]:
    dataset = _get_translation_dataset()
    detected_language = source_language
    detection_confidence = None

    if source_language == "auto":
        from .language_detection_service import get_language_detection_service

        detection = get_language_detection_service(dataset).detect_language(text)
        detected_language = detection["language"]
        detection_confidence = detection["confidence"]

    translated = dataset.translate_phrase(
        text,
        source_lang=detected_language,
        target_lang=target_language,
    )
    return {
        "original": text,
        "translated": translated,
        "source_language": detected_language,
        "target_language": target_language,
        "dataset_loaded": dataset.is_loaded,
        "detection_confidence": detection_confidence,
    }


def translation_dataset_loaded() -> bool:
    return bool(_get_translation_dataset().is_loaded)


def job_directory_path(job_id: str) -> str:
    return _get_file_service().get_job_dir(job_id)


def job_structure_path(job_id: str) -> str:
    return os.path.join(job_directory_path(job_id), "structure.json")


def _sync_generated_outputs(job_id: str, paths: Dict[str, str]) -> None:
    try:
        job = TranslationJob.objects.get(id=job_id)
    except TranslationJob.DoesNotExist:
        return

    output_map = {
        "output_file_path": GeneratedOutput.OutputType.TRANSLATED_PDF,
        "bilingual_file_path": GeneratedOutput.OutputType.BILINGUAL_ALTERNATING,
        "structure_file_path": GeneratedOutput.OutputType.STRUCTURE_JSON,
    }
    for path_key, output_type in output_map.items():
        path = paths.get(path_key)
        if not path or not os.path.exists(path):
            continue
        _, ext = os.path.splitext(path)
        GeneratedOutput.objects.update_or_create(
            job=job,
            output_type=output_type,
            file_path=path,
            defaults={
                "file_format": ext.lstrip(".").lower() or "json",
                "file_size_bytes": os.path.getsize(path),
            },
        )


def _sync_structure_models(job_id: str, structure_path: str) -> None:
    try:
        job = TranslationJob.objects.get(id=job_id)
    except TranslationJob.DoesNotExist:
        return
    try:
        with open(structure_path, "r", encoding="utf-8") as handle:
            structure = json.load(handle)
    except Exception as exc:
        SystemEventLog.objects.create(
            job=job,
            level=SystemEventLog.Level.WARNING,
            event_type="structure_sync_failed",
            message=f"Could not sync structure.json: {exc}",
        )
        return

    seen_pages = set()
    segment_index = 0
    for page_data in structure.get("pages", []):
        page_number = int(page_data.get("page_number") or page_data.get("page") or 0) or 1
        seen_pages.add(page_number)
        page, _ = DocumentPage.objects.update_or_create(
            job=job,
            page_number=page_number,
            defaults={
                "width": page_data.get("width"),
                "height": page_data.get("height"),
                "rotation": int(page_data.get("rotation") or 0),
                "extracted_text": _page_text(page_data),
                "metadata": {
                    "detected_type": structure.get("detected_type"),
                    "warnings": structure.get("warnings", []),
                },
            },
        )
        _sync_page_ocr(job, page, page_data)
        for block in page_data.get("blocks", []):
            if block.get("type") != "text" and block.get("block_type") != "text":
                continue
            lines = block.get("lines") or [
                {
                    "source_text": block.get("source_text"),
                    "translated_text": block.get("translated_text"),
                    "translation_method": block.get("translation_method"),
                    "translation_confidence": block.get("translation_confidence"),
                    "bbox": block.get("bbox"),
                }
            ]
            for line in lines:
                source_text = (line.get("source_text") or line.get("text") or "").strip()
                if not source_text:
                    continue
                translated_text = (line.get("translated_text") or "").strip()
                method = line.get("translation_method") or line.get("cascade_stage") or ""
                confidence = _coerce_float(line.get("translation_confidence"))
                segment_index += 1
                TranslationSegment.objects.update_or_create(
                    job=job,
                    segment_index=segment_index,
                    defaults={
                        "page": page,
                        "source_text": source_text,
                        "translated_text": translated_text,
                        "source_language": job.source_language,
                        "target_language": job.target_language,
                        "method": method,
                        "confidence": confidence,
                        "needs_review": (
                            not translated_text
                            or translated_text == "[UNKNOWN_FOR_REVIEW]"
                            or "UNKNOWN_FOR_REVIEW" in translated_text
                            or method == "unknown_for_review"
                        ),
                        "bbox": line.get("bbox") or block.get("bbox") or [],
                        "metadata": {
                            "block_id": block.get("block_id"),
                            "ocr_confidence": line.get("ocr_confidence") or block.get("ocr_confidence"),
                        },
                    },
                )
    if seen_pages:
        DocumentPage.objects.filter(job=job).exclude(page_number__in=seen_pages).delete()
    TranslationSegment.objects.filter(job=job, segment_index__gt=segment_index).delete()


def _sync_page_ocr(job: TranslationJob, page: DocumentPage, page_data: Dict[str, Any]) -> None:
    confidences = []
    for block in page_data.get("blocks", []):
        value = block.get("ocr_confidence")
        if isinstance(value, (int, float)):
            confidences.append(float(value))
        for line in block.get("lines", []):
            value = line.get("ocr_confidence")
            if isinstance(value, (int, float)):
                confidences.append(float(value))
    confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
    engine = "tesseract" if confidences else "text_extraction"
    OCRResult.objects.update_or_create(
        job=job,
        page=page,
        engine=engine,
        defaults={
            "language_codes": job.ocr_languages,
            "text": page.extracted_text,
            "confidence": confidence,
            "status": OCRResult.Status.PENDING_REVIEW if confidences else OCRResult.Status.ACCEPTED,
            "warnings": page_data.get("warnings", []) or page.metadata.get("warnings", []),
            "raw_data": page_data,
        },
    )


def _page_text(page_data: Dict[str, Any]) -> str:
    lines = []
    for block in page_data.get("blocks", []):
        if block.get("type") != "text" and block.get("block_type") != "text":
            continue
        for line in block.get("lines", []):
            text = (line.get("source_text") or line.get("text") or "").strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _log_failed_job(job_id: str, message: str) -> None:
    try:
        job = TranslationJob.objects.get(id=job_id)
    except TranslationJob.DoesNotExist:
        return
    if SystemEventLog.objects.filter(
        job=job,
        event_type="translation_job_failed",
        message=message or "Translation job failed.",
    ).exists():
        return
    SystemEventLog.objects.create(
        actor=job.owner,
        job=job,
        level=SystemEventLog.Level.ERROR,
        event_type="translation_job_failed",
        message=message or "Translation job failed.",
    )
