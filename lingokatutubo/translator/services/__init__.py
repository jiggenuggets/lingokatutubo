import asyncio
import importlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from translator.models import TranslationJob


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = Path(__file__).resolve().parent


_MAX_WORKERS = int(os.environ.get("TRANSLATION_WORKERS", "2"))
_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, _MAX_WORKERS))
_CALLBACK_REGISTERED = False


def _ensure_services_path() -> None:
    if str(SERVICES_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICES_DIR))


def _service_module(module_name: str):
    _ensure_services_path()
    return importlib.import_module(module_name)


def _get_file_service():
    module = _service_module("file_service")
    current = getattr(module, "_file_service", None)
    media_root = str(settings.MEDIA_ROOT)
    if (
        current is None
        or getattr(current, "storage_layout", "") != "django"
        or str(getattr(current, "upload_dir", "")) != media_root
    ):
        module._file_service = module.FileService(
            upload_dir=media_root,
            storage_layout="django",
        )
    return module._file_service


def _get_file_type_enum():
    return _service_module("models").FileType


def _get_pipeline_service():
    return _service_module("pipeline_service").get_pipeline_service()


def _get_translation_dataset():
    return _service_module("translation_dataset").get_translation_dataset()


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


def register_pipeline_callback() -> None:
    global _CALLBACK_REGISTERED
    if _CALLBACK_REGISTERED:
        return
    _get_pipeline_service().set_job_update_callback(sync_pipeline_job)
    _CALLBACK_REGISTERED = True


def start_translation_job(job: TranslationJob) -> None:
    register_pipeline_callback()
    file_type = _get_file_type_enum()(job.file_type)
    _EXECUTOR.submit(
        _run_translation_job,
        job.job_id,
        job.input_file_path,
        file_type,
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
        asyncio.run(
            _get_pipeline_service().process_translation(
                job_id=job_id,
                input_file_path=input_file_path,
                file_type=file_type,
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
        from language_detection_service import get_language_detection_service

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
