"""
Bagobo-Tagabawa Document Translator Backend API
FastAPI application with document translation endpoints
"""

import sys
import os

# Force Python UTF-8 mode on Windows to prevent 'charmap' codec errors
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import fastapi
import fastapi.middleware.cors
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import json
import re
import uuid
from typing import Optional

from models import (
    FileType,
    TranslateRequest,
    TranslateResponse,
    JobStatus as JobStatusModel,
)
from file_service import get_file_service
from pipeline_service import get_pipeline_service
from translation_dataset import get_translation_dataset

# Initialize FastAPI app
app = fastapi.FastAPI(
    title="Bagobo-Tagabawa Document Translator",
    description="API for translating documents to/from Bagobo-Tagabawa language",
    version="0.1.0"
)

# Add CORS middleware - explicitly allow Next.js frontend
app.add_middleware(
    fastapi.middleware.cors.CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "*",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get services
file_service = get_file_service()
pipeline_service = get_pipeline_service()
translation_dataset = get_translation_dataset()
PREVIEW_IMAGE_NAME_RE = re.compile(r"^(?:original|translated)_page_\d+\.png$")
DEFAULT_JOB_RETENTION_SECONDS = 7 * 24 * 60 * 60


def _job_retention_seconds() -> int:
    raw_value = os.environ.get("JOB_RETENTION_SECONDS")
    if not raw_value:
        return DEFAULT_JOB_RETENTION_SECONDS
    try:
        return max(0, int(raw_value))
    except ValueError:
        return DEFAULT_JOB_RETENTION_SECONDS


def _cleanup_old_job_files() -> None:
    """Best-effort retention cleanup; never block a new translation."""
    try:
        cleanup_result = pipeline_service.cleanup_old_job_files(_job_retention_seconds())
        removed = cleanup_result.get("removed", [])
        if removed:
            print(f"[Cleanup] Removed {len(removed)} old job directories")
    except Exception as e:
        print(f"[Cleanup] Skipped old job cleanup: {e}")


@app.get("/health")
async def health() -> dict:
    """Health check endpoint"""
    return {
        "status": "ok",
        "translation_dataset_loaded": translation_dataset.is_loaded
    }


@app.post("/translate")
async def translate_document(
    file: fastapi.UploadFile = fastapi.File(...),
    source_language: str = fastapi.Form("auto"),
    target_language: str = fastapi.Form("tagabawa"),
    ocr_languages: Optional[str] = fastapi.Form(None),
) -> TranslateResponse:
    """
    Upload a document and start translation
    
    Supported file types: PDF, DOCX, JPG, PNG
    
    Args:
        file: The document file
        source_language: Source language (default: english)
        target_language: Target language (default: tagabawa)
        ocr_languages: Optional comma/plus separated OCR language list for scans
    
    Returns:
        Job ID and initial status
    """
    try:
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        
        # Read uploaded file
        file_content = await file.read()
        
        if not file_content:
            return TranslateResponse(
                job_id=job_id,
                status="failed",
                message="Empty file uploaded"
            )
        
        # Detect file type
        file_type = file_service.get_file_type(file.filename)
        if not file_type:
            return TranslateResponse(
                job_id=job_id,
                status="failed",
                message=f"Unsupported file type: {file.filename}"
            )

        _cleanup_old_job_files()
        
        # Save the file
        input_path = await file_service.save_upload(
            file_content,
            file.filename,
            job_id
        )
        
        print(
            f"[Translate] job_id={job_id} filename={file.filename} "
            f"source={source_language} target={target_language} "
            f"ocr_languages={ocr_languages}"
        )
        
        # Start pipeline in background
        # Note: In production, use Celery/RQ for background tasks
        import asyncio
        asyncio.create_task(
            pipeline_service.process_translation(
                job_id=job_id,
                input_file_path=input_path,
                file_type=file_type,
                source_language=source_language,
                target_language=target_language,
                ocr_languages=ocr_languages,
            )
        )
        
        return TranslateResponse(
            job_id=job_id,
            status="processing",
            message="Translation started"
        )
    
    except Exception as e:
        print(f"[API] Error in /translate: {e}")
        return TranslateResponse(
            job_id="",
            status="failed",
            message=str(e)
        )


@app.get("/status/{job_id}")
@app.get("/jobs/{job_id}")
async def get_status(job_id: str) -> dict:
    """
    Get the status of a translation job
    
    Args:
        job_id: The job ID from /translate response
    
    Returns:
        Job status, progress, and error details if any
    """
    job_status = pipeline_service.get_job_status(job_id)

    if not job_status:
        return {
            "job_id": job_id,
            "status": "not_found",
            "message": "Job not found",
            "output_file": None,
            "progress_percent": 0,
            "current_phase": "not_found",
            "current_step": "Job not found",
            "phase_message": "Job not found",
            "error": "Job not found"
        }

    meta = job_status.metadata or {}

    # Determine output file if job is completed
    output_file = None
    if job_status.status == "completed":
        output_path = pipeline_service.get_job_output(job_id)
        if output_path and os.path.exists(output_path):
            output_file = output_path

    # Build human-friendly status message
    status_messages = {
        "queued": "Job queued",
        "processing": "Translation in progress",
        "completed": "Translation complete",
        "failed": "Translation failed",
    }
    message = status_messages.get(job_status.status, job_status.status)

    return {
        "job_id": job_id,
        "status": job_status.status,
        "message": job_status.phase_message or message,
        "output_file": output_file,
        "progress": job_status.progress,
        "progress_percent": 100 if job_status.status == "completed" else job_status.progress,
        "current_phase": job_status.current_phase,
        "current_step": job_status.current_step,
        "phase_message": job_status.phase_message or message,
        "detection_type": job_status.detection_type.value if job_status.detection_type else None,
        "file_type": job_status.file_type.value if job_status.file_type else None,
        "error": job_status.error,
        "detected_language": meta.get("detected_language"),
        "detection_confidence": meta.get("detection_confidence"),
        "is_mixed_language": meta.get("is_mixed_language", False),
        "metadata": meta,
    }


@app.get("/structure/{job_id}")
async def get_structure(job_id: str):
    """
    Return the structured document JSON for a job.

    Shape: {job_id, status, detected_type, pages: [{page_number, width, height,
    rotation, blocks: [{block_id, block_type, bbox, source_text,
    translated_text, translation_method, cascade_stage, translation_confidence,
    lines, metadata, detected_language, ocr_confidence}]}], warnings: []}.

    For digital PDFs, ocr_confidence is null. For scanned PDFs/images,
    Tesseract OCR fills blocks when text is found and warnings explain OCR
    fallback or failure cases.
    """
    structure_path = pipeline_service.get_structure_path(job_id)
    job_status = pipeline_service.get_job_status(job_id)

    if not os.path.exists(structure_path):
        if not job_status:
            return JSONResponse(
                status_code=404,
                content={"error": "Job not found", "detail": f"No job with id {job_id}"},
            )
        return JSONResponse(
            status_code=404,
            content={
                "error": "Structure not yet available",
                "detail": f"Job status is {job_status.status}; extraction has not produced structure.json yet",
            },
        )

    try:
        with open(structure_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to read structure.json", "detail": str(e)},
        )

    # Reflect the live job state rather than the snapshot taken at extraction time.
    if job_status:
        data["status"] = job_status.status

    return data


@app.get("/preview/{job_id}")
async def get_preview(job_id: str):
    """
    Get preview images for a completed translation
    
    Args:
        job_id: The job ID
    
    Returns:
        Preview data with original and translated page images
    """
    job_status = pipeline_service.get_job_status(job_id)
    
    if not job_status:
        return JSONResponse(
            status_code=404,
            content={"error": "Job not found"}
        )
    
    if job_status.status != "completed":
        return JSONResponse(
            status_code=400,
            content={"error": f"Job status is {job_status.status}, not completed"}
        )
    
    preview_original = job_status.metadata.get("preview_original", [])
    preview_translated = job_status.metadata.get("preview_translated", [])
    bilingual_first_page = job_status.metadata.get("bilingual_first_page", {"blocks": []})

    left_page_preview = None
    if preview_original:
        first_image_path = str(preview_original[0]).replace("\\", "/")
        first_image_name = first_image_path.split("/")[-1]
        if first_image_name:
            left_page_preview = f"/preview-image/{job_id}/{first_image_name}"
    
    # Convert file paths to URLs (in production, serve from object storage)
    return {
        "job_id": job_id,
        "left_page_preview": left_page_preview,
        "bilingual_first_page": bilingual_first_page,
        "original_pages": preview_original,
        "translated_pages": preview_translated,
        "page_count": max(len(preview_original), len(preview_translated))
    }


@app.get("/download/{job_id}")
async def download_document(job_id: str):
    """
    Download the translated document
    
    Supports:
    - ?format=translated - translated PDF only
    - ?format=bilingual - side-by-side original + translated
    
    Args:
        job_id: The job ID
        format: Output format (translated or bilingual)
    
    Returns:
        PDF file download
    """
    print(f"[Download] job_id={job_id}")
    job_status = pipeline_service.get_job_status(job_id)

    if not job_status:
        print(f"[Download] Job not found: {job_id}")
        return JSONResponse(
            status_code=404,
            content={"error": "Job not found"}
        )

    if job_status.status != "completed":
        print(f"[Download] Job not completed: status={job_status.status}, error={job_status.error}")
        return JSONResponse(
            status_code=400,
            content={"error": f"Job not completed yet: {job_status.status}", "detail": job_status.error}
        )

    # Get output file
    output_path = pipeline_service.get_job_output(job_id)
    print(f"[Download] expected path={output_path}")
    print(f"[Download] exists={os.path.exists(output_path) if output_path else False}")

    if not output_path or not os.path.exists(output_path):
        print(f"[Download] File missing at: {output_path}")
        return JSONResponse(
            status_code=404,
            content={"error": "Translated PDF not found", "detail": f"Expected at: {output_path}"}
        )
    
    file_size = os.path.getsize(output_path)
    filename = f"translated_{job_id[:8]}.pdf"
    
    return FileResponse(
        path=output_path,
        filename=filename,
        media_type="application/pdf",
        headers={
            "Content-Length": str(file_size),
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@app.post("/quick-translate")
async def quick_translate(
    text: str = fastapi.Query(...),
    source_language: str = fastapi.Query("english"),
    target_language: str = fastapi.Query("tagabawa")
) -> dict:
    """
    Translate a single phrase across any supported language pair.

    Supported languages: english, tagabawa, filipino, cebuano
    Use source_language="auto" to auto-detect.
    """
    try:
        from language_detection_service import get_language_detection_service
        lang_service = get_language_detection_service(translation_dataset)

        detected_language = source_language
        detection_confidence = None

        if source_language == "auto":
            detection = lang_service.detect_language(text)
            detected_language = detection["language"]
            detection_confidence = detection["confidence"]

        translated = translation_dataset.translate_phrase(
            text,
            source_lang=detected_language,
            target_lang=target_language,
        )

        return {
            "original": text,
            "translated": translated,
            "source_language": detected_language,
            "target_language": target_language,
            "dataset_loaded": translation_dataset.is_loaded,
            "detection_confidence": detection_confidence,
        }

    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


# Serve preview images from the jobs directory
@app.get("/preview-image/{job_id}/{image_name}")
async def serve_preview_image(job_id: str, image_name: str):
    """
    Serve preview images for the frontend
    
    Args:
        job_id: Job ID
        image_name: Image filename (original_page_0.png, translated_page_0.png, etc)
    
    Returns:
        PNG image file
    """
    if not PREVIEW_IMAGE_NAME_RE.fullmatch(image_name):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid preview image name"}
        )

    job_dir = file_service.get_job_dir(job_id)
    preview_dir = os.path.join(job_dir, "preview")
    image_path = os.path.join(preview_dir, image_name)
    
    # Security: validate path is within job_dir
    if not os.path.abspath(image_path).startswith(os.path.abspath(job_dir)):
        return JSONResponse(
            status_code=403,
            content={"error": "Access denied"}
        )
    
    if not os.path.exists(image_path):
        return JSONResponse(
            status_code=404,
            content={"error": "Image not found"}
        )
    
    return FileResponse(
        path=image_path,
        media_type="image/png"
    )


@app.get("/")
async def root():
    """Root endpoint with API documentation"""
    return {
        "name": "Bagobo-Tagabawa Document Translator API",
        "version": "0.1.0",
        "endpoints": {
            "POST /translate": "Upload document for translation",
            "GET /status/{job_id}": "Get translation job status",
            "GET /structure/{job_id}": "Get structured page/block JSON for a job",
            "GET /preview/{job_id}": "Get preview images",
            "GET /download/{job_id}": "Download translated document",
            "POST /quick-translate": "Translate a single phrase",
            "GET /health": "Health check"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
