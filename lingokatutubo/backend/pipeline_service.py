"""
Translation pipeline coordinator
Orchestrates the entire translation workflow
"""

import asyncio
import os
from typing import Optional, Dict, Any, List, Sequence, Union
from datetime import datetime
import json
import sys
from pathlib import Path

from models import FileType, DetectionType
from file_service import get_file_service
from detection_service import get_detection_service
from extraction_service import get_extraction_service
from reconstruction_service import get_reconstruction_service
from translation_dataset import UNKNOWN_FOR_REVIEW, get_translation_dataset
from language_detection_service import get_language_detection_service
from ocr_stage import OCRUnavailableError, get_ocr_service


PIPELINE_PHASES = {
    "queued": {
        "percent": 0,
        "step": "Queued for processing",
        "message": "Document is waiting to be processed.",
    },
    "detecting": {
        "percent": 10,
        "step": "Detecting document type",
        "message": "Detecting document type...",
    },
    "extracting": {
        "percent": 25,
        "step": "Extracting text and layout",
        "message": "Extracting text and layout...",
    },
    "ocr": {
        "percent": 25,
        "step": "Running OCR for scanned document",
        "message": "Running OCR for scanned document...",
    },
    "translating": {
        "percent": 50,
        "step": "Translating extracted text",
        "message": "Translating document text...",
    },
    "reconstructing": {
        "percent": 70,
        "step": "Reconstructing translated PDF",
        "message": "Reconstructing translated PDF...",
    },
    "preview_generation": {
        "percent": 85,
        "step": "Creating document previews",
        "message": "Creating document previews...",
    },
    "bilingual_output": {
        "percent": 95,
        "step": "Preparing bilingual output",
        "message": "Preparing bilingual output...",
    },
    "completed": {
        "percent": 100,
        "step": "Completed",
        "message": "Translation complete.",
    },
}


class JobStatus:
    """Tracks the status of a translation job"""
    
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.status = "queued"  # queued, processing, completed, failed
        self.progress = 0
        self.current_phase = "queued"
        self.current_step = PIPELINE_PHASES["queued"]["step"]
        self.phase_message = PIPELINE_PHASES["queued"]["message"]
        self.error = None
        self.detection_type = None
        self.file_type = None
        self.created_at = datetime.now()
        self.completed_at = None
        self.metadata = {}


class PipelineService:
    """Orchestrates the document translation pipeline"""
    
    def __init__(self):
        self.file_service = get_file_service()
        self.detection_service = get_detection_service()
        self.extraction_service = get_extraction_service()
        self.reconstruction_service = get_reconstruction_service()
        self.translation_dataset = get_translation_dataset()
        self.language_service = get_language_detection_service(self.translation_dataset)
        self.ocr_service = get_ocr_service()

        self.jobs = {}  # job_id -> JobStatus

    @staticmethod
    def _run_ocr_for_scanned(input_file_path: str) -> dict:
        """
        Run OCR from the OCR stage module for scanned/image input.
        """
        project_root = Path(__file__).resolve().parents[1]
        if str(project_root) not in sys.path:
            sys.path.append(str(project_root))

        from ocr_stage.services.ocr_service import run_ocr

        return run_ocr(input_file_path)

    @staticmethod
    def _set_job_phase(job: JobStatus, phase: str) -> None:
        phase_info = PIPELINE_PHASES.get(phase, PIPELINE_PHASES["queued"])
        job.current_phase = phase
        job.current_step = phase_info["step"]
        job.phase_message = phase_info["message"]
        job.progress = int(phase_info["percent"])
        job.metadata["current_phase"] = job.current_phase
        job.metadata["current_step"] = job.current_step
        job.metadata["phase_message"] = job.phase_message
        job.metadata["progress_percent"] = job.progress
    
    async def process_translation(
        self,
        job_id: str,
        input_file_path: str,
        file_type: FileType,
        source_language: str = "auto",
        target_language: str = "tagabawa",
        ocr_languages: Optional[Union[str, Sequence[str]]] = None,
    ) -> bool:
        """
        Main translation pipeline
        
        Args:
            job_id: Unique job identifier
            input_file_path: Path to uploaded file
            file_type: Type of file (PDF, DOCX, JPG, PNG)
            source_language: Source language
            target_language: Target language
            ocr_languages: Optional OCR language names/codes for scanned input
        
        Returns:
            True if successful
        """
        job = JobStatus(job_id)
        job.file_type = file_type
        self.jobs[job_id] = job
        
        try:
            job.status = "processing"
            self._set_job_phase(job, "detecting")

            print(f"[Pipeline] Job {job_id} started")

            # Phase 1: Detect file type (digital vs scanned)
            print(f"[Pipeline] Phase 1: Detecting file type for {job_id}")
            
            if file_type == FileType.PDF:
                job.detection_type = self.detection_service.detect_pdf_type(input_file_path)
            elif file_type == FileType.DOCX:
                job.detection_type = self.detection_service.detect_docx_type(input_file_path)
            else:  # JPG, PNG
                job.detection_type = self.detection_service.detect_image_type(input_file_path)
            
            print(f"[Pipeline] Detected: {job.detection_type}")
            
            # Phase 2: Extract text and layout
            print(f"[Pipeline] Phase 2: Extracting text and layout")
            self._set_job_phase(job, "extracting")
            
            layout_data = []
            
            ocr_unavailable_msg: Optional[str] = None
            requested_ocr_languages = self._requested_ocr_languages(
                source_language=source_language,
                ocr_languages=ocr_languages,
            )

            if job.detection_type == DetectionType.DIGITAL:
                if file_type == FileType.PDF:
                    layout_data = self.extraction_service.extract_pdf_text_and_layout(input_file_path)
                elif file_type == FileType.DOCX:
                    layout_data = self.extraction_service.extract_docx_text_and_layout(input_file_path)
            else:
                # Scanned input: route through Tesseract OCR. No mock fallback.
                self._set_job_phase(job, "ocr")
                try:
                    if file_type == FileType.PDF:
                        print(f"[Pipeline] SCANNED PDF - running Tesseract OCR")
                        layout_data = self.ocr_service.extract_pdf_text_and_layout(
                            input_file_path,
                            languages=requested_ocr_languages,
                        )
                    elif file_type in (FileType.JPG, FileType.PNG):
                        print(f"[Pipeline] Image input - running Tesseract OCR")
                        layout_data = self.ocr_service.extract_image_text_and_layout(
                            input_file_path,
                            languages=requested_ocr_languages,
                        )
                    else:
                        layout_data = []
                except OCRUnavailableError as e:
                    ocr_unavailable_msg = str(e)
                    print(f"[Pipeline] OCR unavailable: {e}")
                    layout_data = []
                except Exception as e:
                    print(f"[Pipeline] OCR error: {e}")
                    ocr_unavailable_msg = f"OCR error: {e}"
                    layout_data = []

            # layout_data may legitimately be empty for scanned input when OCR
            # finds no text — defer the failure to after structure.json is saved
            # so callers can inspect the warnings.
            if layout_data is None:
                layout_data = []
            job.metadata["layout_blocks"] = len(layout_data)

            # Persist a structured view of the document for the /structure endpoint.
            # Always runs (even on empty/failed OCR) so callers see the warnings.
            try:
                structure_path = self._build_and_save_structure(
                    job_id=job_id,
                    job=job,
                    file_type=file_type,
                    layout_data=layout_data,
                    input_file_path=input_file_path,
                    ocr_unavailable_msg=ocr_unavailable_msg,
                )
                job.metadata["structure_file"] = structure_path
            except Exception as struct_err:
                print(f"[Pipeline] Failed to build structure.json: {struct_err}")

            # Now enforce: we need at least one extractable text block to
            # continue. Fail loudly with an actionable message rather than
            # silently producing placeholder output.
            text_block_count = sum(
                1
                for page in layout_data
                for block in page.get("blocks", [])
                if block.get("type") == "text"
                and any((line.get("text") or "").strip() for line in block.get("lines", []))
            )
            if text_block_count == 0:
                if ocr_unavailable_msg:
                    raise Exception(
                        f"OCR engine unavailable: {ocr_unavailable_msg}. "
                        "structure.json was written with a warning."
                    )
                if job.detection_type == DetectionType.SCANNED:
                    raise Exception(
                        "OCR produced no text from this scanned document. "
                        "Page may be blank, low-quality, or in an unsupported "
                        "language. structure.json contains warnings."
                    )
                raise Exception("Failed to extract layout (no text blocks found)")

            # Phase 2.5: Auto-detect source language if requested
            if source_language == "auto":
                print(f"[Pipeline] Phase 2.5: Auto-detecting source language")
                text_samples = [
                    line.get("text", "")
                    for page in layout_data[:3]
                    for block in page.get("blocks", [])
                    if block.get("type") == "text"
                    for line in block.get("lines", [])
                    if line.get("text", "").strip()
                ]
                lang_result = self.language_service.detect_document_language(text_samples)
                detected = lang_result["primary_language"]
                confidence = lang_result["confidence"]
                print(f"[Pipeline] Detected: {detected} (confidence: {confidence:.2f})")
                job.metadata["detected_language"] = detected
                job.metadata["detection_confidence"] = round(confidence, 3)
                job.metadata["is_mixed_language"] = lang_result.get("is_mixed", False)
                job.metadata["language_distribution"] = lang_result.get("language_distribution", {})
                source_language = detected
                print(f"[Pipeline] Language detected: {detected} (confidence: {confidence:.2f})")
            else:
                job.metadata["detected_language"] = source_language
                job.metadata["detection_confidence"] = 1.0
                print(f"[Pipeline] Language set manually: {source_language}")

            # Phase 3: Translate text
            print(f"[Pipeline] Phase 3: Translating text ({source_language} -> {target_language})")
            self._set_job_phase(job, "translating")

            translation_warnings: List[str] = []
            translations = self._translate_layout(
                layout_data,
                source_language,
                target_language,
                translation_warnings=translation_warnings,
            )
            if translation_warnings:
                job.metadata["translation_warnings"] = translation_warnings
                self._append_structure_warnings(job_id, translation_warnings)

            # Debug: log first 10 translation pairs to backend console
            _sample = list(translations.items())[:10]
            for _orig, _xlat in _sample:
                print(f'[Pipeline] Translated: "{_orig}" -> "{_xlat}"')

            job.metadata["translated_blocks"] = len(translations)
            job.metadata["bilingual_first_page"] = self._build_bilingual_first_page(
                layout_data,
                translations,
            )
            self._attach_translations_to_structure(job_id, translations)

            # Phase 4: Reconstruct document
            print(f"[Pipeline] Phase 4: Reconstructing document")
            self._set_job_phase(job, "reconstructing")
            
            output_pdf_path = self.file_service.get_output_path(job_id, "translated.pdf")
            layout_warnings: List[str] = []
            
            if file_type == FileType.PDF:
                success = self.reconstruction_service.reconstruct_pdf(
                    input_file_path,
                    layout_data,
                    translations,
                    output_pdf_path,
                    is_scanned=job.detection_type == DetectionType.SCANNED,
                    layout_warnings=layout_warnings,
                )
            else:
                # For DOCX/images, create a simple PDF output
                success = self._create_output_pdf(
                    layout_data,
                    translations,
                    output_pdf_path,
                    input_file_path=input_file_path,
                    file_type=file_type,
                    layout_warnings=layout_warnings,
                )
            
            if not success:
                raise Exception("Failed to reconstruct PDF")

            if layout_warnings:
                job.metadata["layout_warnings"] = layout_warnings
                self._append_structure_warnings(job_id, layout_warnings)

            if not os.path.exists(output_pdf_path):
                raise Exception(f"Output PDF was not created at: {output_pdf_path}")

            print(f"[Pipeline] Output PDF created: {output_pdf_path} ({os.path.getsize(output_pdf_path)} bytes)")

            # Phase 5: Create bilingual preview
            print(f"[Pipeline] Phase 5: Creating preview")
            self._set_job_phase(job, "preview_generation")
            
            preview_dir = os.path.join(self.file_service.get_job_dir(job_id), "preview")
            # Cap at 20 pages to keep preview generation bounded for large PDFs.
            # The /preview endpoint reports every generated page, so the
            # frontend viewer can paginate up to this limit.
            original_previews = self.reconstruction_service.create_preview_images(
                input_file_path, preview_dir, max_pages=20, prefix="original"
            )
            translated_previews = self.reconstruction_service.create_preview_images(
                output_pdf_path, preview_dir, max_pages=20, prefix="translated"
            )
            
            job.metadata["preview_original"] = original_previews
            job.metadata["preview_translated"] = translated_previews

            # Phase 6: Create bilingual PDF
            print(f"[Pipeline] Phase 6: Creating bilingual PDF")
            self._set_job_phase(job, "bilingual_output")
            
            bilingual_path = self.file_service.get_output_path(job_id, "bilingual.pdf")
            if file_type == FileType.PDF:
                self.reconstruction_service.create_bilingual_pdf(
                    input_file_path,
                    output_pdf_path,
                    bilingual_path
                )
                job.metadata["bilingual_pdf"] = bilingual_path
            
            job.status = "completed"
            self._set_job_phase(job, "completed")
            job.completed_at = datetime.now()
            
            print(f"[Pipeline] Job {job_id} completed successfully")
            return True
        
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.phase_message = str(e)
            job.current_step = "Translation failed"
            job.metadata["phase_message"] = job.phase_message
            job.metadata["current_step"] = job.current_step
            job.metadata["progress_percent"] = job.progress
            print(f"[Pipeline] Job {job_id} failed: {e}")
            return False
    
    def _translate_layout(
        self,
        layout_data: list,
        source_lang: str,
        target_lang: str,
        translation_warnings: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, str]]:
        """
        Translate all text in layout data
        
        Returns:
            Dict mapping line/block/source keys to translation records.
        """
        translations = {}
        
        for page_index, page_data in enumerate(layout_data):
            blocks = page_data.get("blocks", [])
            
            for block_index, block in enumerate(blocks):
                if block.get("type") != "text":
                    continue
                
                lines = block.get("lines", [])
                
                for line_index, line in enumerate(lines):
                    original = line.get("text", "").strip()
                    if not original:
                        continue
                    
                    try:
                        if hasattr(self.translation_dataset, "translate_phrase_with_metadata"):
                            translation_meta = self.translation_dataset.translate_phrase_with_metadata(
                                original,
                                source_lang=source_lang,
                                target_lang=target_lang
                            )
                        else:
                            translated = self.translation_dataset.translate_phrase(
                                original,
                                source_lang=source_lang,
                                target_lang=target_lang
                            )
                            translation_meta = {
                                "translated": translated,
                                "method": "unknown",
                                "cascade_stage": "unknown",
                                "confidence": None,
                            }
                    except Exception as exc:
                        message = (
                            f"Page {page_index + 1}, block {block_index + 1}, line {line_index + 1}: "
                            f"translation failed for {source_lang}->{target_lang}: {exc}"
                        )
                        if translation_warnings is not None and message not in translation_warnings:
                            translation_warnings.append(message)
                        translation_meta = {
                            "translated": UNKNOWN_FOR_REVIEW,
                            "method": "unknown_for_review",
                            "cascade_stage": "unknown_for_review",
                            "confidence": 0.0,
                        }

                    block_id = f"{page_index}_{block_index}"
                    line_id = f"{page_index}_{block_index}_{line_index}"
                    record = {
                        "original": original,
                        "translated": translation_meta.get("translated", original),
                        "method": translation_meta.get("method", "unknown"),
                        "cascade_stage": translation_meta.get("cascade_stage", translation_meta.get("method", "unknown")),
                        "confidence": translation_meta.get("confidence"),
                        "source_language": source_lang,
                        "target_language": target_lang,
                    }
                    translations[line_id] = record
                    translations[original] = record
                    translations[original.strip()] = record
                    # Legacy block-level key for older reconstruction callers.
                    translations[block_id] = record
        
        return translations
    
    # ------------------------------------------------------------------
    # Structure JSON (consumed by GET /structure/{job_id})
    # ------------------------------------------------------------------

    @staticmethod
    def _split_language_values(
        languages: Optional[Union[str, Sequence[str]]]
    ) -> List[str]:
        if languages is None:
            return []
        if isinstance(languages, str):
            values = [languages]
        else:
            values = [str(value) for value in languages]

        parts: List[str] = []
        for value in values:
            normalized = value
            for sep in ("+", ",", ";", "|", "/"):
                normalized = normalized.replace(sep, ",")
            parts.extend(
                part.strip()
                for part in normalized.split(",")
                if part.strip()
            )
        return parts

    def _requested_ocr_languages(
        self,
        source_language: str,
        ocr_languages: Optional[Union[str, Sequence[str]]],
    ) -> Optional[List[str]]:
        explicit_ocr_languages = self._split_language_values(ocr_languages)
        if explicit_ocr_languages:
            return explicit_ocr_languages

        source_languages = self._split_language_values(source_language)
        source_languages = [
            language
            for language in source_languages
            if language.lower() not in {"auto", "detect", "unknown"}
        ]
        return source_languages or None

    def _build_and_save_structure(
        self,
        job_id: str,
        job: "JobStatus",
        file_type: FileType,
        layout_data: list,
        input_file_path: str,
        ocr_unavailable_msg: Optional[str] = None,
    ) -> str:
        """
        Build the per-job structure.json from extracted layout data.

        Works the same way for digital and scanned input: blocks/lines come
        directly from layout_data. For scanned input, `ocr_confidence` is
        populated from the OCR engine's per-block confidence; for digital it
        stays null. Page-level OCR errors and OCR unavailability are surfaced
        in `warnings`.
        """
        detected_type = self._format_detected_type(file_type, job.detection_type)
        is_scanned = job.detection_type == DetectionType.SCANNED
        warnings: list = []
        pages_out: list = []

        def add_warning(message: str) -> None:
            if message and message not in warnings:
                warnings.append(message)

        if ocr_unavailable_msg:
            add_warning(
                "OCR engine unavailable; scanned input could not be processed. "
                f"Detail: {ocr_unavailable_msg}"
            )
        elif is_scanned and not self.ocr_service.is_available():
            add_warning(
                "Tesseract OCR is not available on this system. "
                "Install Tesseract OCR to enable scanned-PDF extraction."
            )

        total_text_blocks = 0
        for page_data in layout_data:
            page_idx = page_data.get("page", 0)
            blocks_out = []
            block_counter = 0

            page_err = page_data.get("ocr_error")
            if page_err:
                add_warning(f"Page {page_idx + 1}: OCR error: {page_err}")
            page_warn = page_data.get("ocr_warning")
            if page_warn:
                add_warning(f"Page {page_idx + 1}: {page_warn}")

            for block in page_data.get("blocks", []):
                block_type = block.get("type", "unknown")
                block_counter += 1

                if block_type == "text":
                    text = " ".join(
                        line.get("text", "").strip()
                        for line in block.get("lines", [])
                        if line.get("text", "").strip()
                    ).strip()
                    if not text:
                        continue

                    try:
                        lang_result = self.language_service.detect_language(text)
                        detected_lang = lang_result.get("language", "unknown")
                    except Exception:
                        detected_lang = "unknown"

                    # Only scanned input carries a meaningful confidence value.
                    ocr_conf = None
                    raw_conf = block.get("confidence")
                    if is_scanned and raw_conf is not None:
                        try:
                            ocr_conf = round(float(raw_conf), 4)
                        except (TypeError, ValueError):
                            ocr_conf = None

                    bbox = block.get("bbox") or []
                    blocks_out.append({
                        "block_id": f"p{page_idx + 1}_b{block_counter}",
                        "block_type": "text",
                        "type": "text",
                        "bbox": self._json_safe_value(bbox),
                        "source_text": text,
                        "translated_text": None,
                        "translation_method": None,
                        "cascade_stage": None,
                        "translation_confidence": None,
                        "detected_language": detected_lang,
                        "ocr_confidence": ocr_conf,
                        "metadata": {"layout_block_index": block_counter - 1},
                        "lines": self._structure_lines(block),
                    })
                    total_text_blocks += 1
                    continue

                bbox = block.get("bbox") or []
                blocks_out.append({
                    "block_id": f"p{page_idx + 1}_b{block_counter}",
                    "block_type": block_type,
                    "type": block_type,
                    "bbox": self._json_safe_value(bbox),
                    "source_text": None,
                    "translated_text": None,
                    "translation_method": None,
                    "cascade_stage": None,
                    "translation_confidence": None,
                    "detected_language": None,
                    "ocr_confidence": None,
                    "metadata": self._block_metadata(block),
                })

            pages_out.append({
                "page_number": page_idx + 1,
                "width": page_data.get("width"),
                "height": page_data.get("height"),
                "rotation": page_data.get("rotation", 0),
                "blocks": blocks_out,
            })

        # If scanned and OCR found nothing, still surface page geometry so
        # the frontend can render an empty preview, and add a clear warning.
        if is_scanned and total_text_blocks == 0:
            if not pages_out and file_type == FileType.PDF:
                try:
                    import fitz
                    doc = fitz.open(input_file_path)
                    for i in range(doc.page_count):
                        p = doc[i]
                        pages_out.append({
                            "page_number": i + 1,
                            "width": p.rect.width,
                            "height": p.rect.height,
                            "blocks": [],
                        })
                    doc.close()
                except Exception as e:
                    add_warning(f"Could not read source page dimensions: {e}")
            if not ocr_unavailable_msg:
                add_warning(
                    "OCR did not extract any text. The page may be blank, "
                    "low-quality, or in an unsupported language."
                )

        structure = {
            "job_id": job_id,
            "status": job.status,
            "detected_type": detected_type,
            "pages": pages_out,
            "warnings": warnings,
        }

        structure_path = self.get_structure_path(job_id)
        os.makedirs(os.path.dirname(structure_path), exist_ok=True)
        with open(structure_path, "w", encoding="utf-8") as f:
            json.dump(structure, f, ensure_ascii=False, indent=2)
        print(f"[Pipeline] structure.json saved: {structure_path}")
        return structure_path

    def _attach_translations_to_structure(
        self,
        job_id: str,
        translations: Dict[str, Dict[str, Any]],
    ) -> None:
        """Attach translated text and cascade metadata to structure.json."""
        structure_path = self.get_structure_path(job_id)
        if not os.path.exists(structure_path):
            return

        try:
            with open(structure_path, "r", encoding="utf-8") as f:
                structure = json.load(f)

            for page_idx, page in enumerate(structure.get("pages", [])):
                for block in page.get("blocks", []):
                    if block.get("type") != "text":
                        continue
                    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
                    block_idx = metadata.get("layout_block_index")
                    if block_idx is None:
                        block_idx = max(0, int(str(block.get("block_id", "b1")).rsplit("b", 1)[-1]) - 1)

                    line_translations: List[str] = []
                    line_methods: List[str] = []
                    line_confidences: List[float] = []

                    for line_idx, line in enumerate(block.get("lines", [])):
                        source_text = (line.get("text") or "").strip()
                        record = self._translation_record_for_line(
                            translations,
                            page_idx,
                            int(block_idx),
                            line_idx,
                            source_text,
                        )
                        if not record:
                            line["translated_text"] = None
                            line["translation_method"] = None
                            line["cascade_stage"] = None
                            line["translation_confidence"] = None
                            continue

                        translated = record.get("translated")
                        method = record.get("method")
                        confidence = record.get("confidence")
                        line["translated_text"] = translated
                        line["translation_method"] = method
                        line["cascade_stage"] = record.get("cascade_stage", method)
                        line["translation_confidence"] = confidence

                        if translated:
                            line_translations.append(str(translated))
                        if method:
                            line_methods.append(str(method))
                        if isinstance(confidence, (int, float)):
                            line_confidences.append(float(confidence))

                    block["translated_text"] = " ".join(line_translations).strip() or None
                    block["translation_method"] = (
                        line_methods[0]
                        if line_methods and all(method == line_methods[0] for method in line_methods)
                        else ("mixed" if line_methods else None)
                    )
                    block["cascade_stage"] = block["translation_method"]
                    block["translation_confidence"] = (
                        round(sum(line_confidences) / len(line_confidences), 4)
                        if line_confidences
                        else None
                    )

            with open(structure_path, "w", encoding="utf-8") as f:
                json.dump(structure, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Pipeline] Failed to attach translations to structure.json: {e}")

    @staticmethod
    def _translation_record_for_line(
        translations: Dict[str, Dict[str, Any]],
        page_idx: int,
        block_idx: int,
        line_idx: int,
        source_text: str,
    ) -> Optional[Dict[str, Any]]:
        candidate_keys = [
            f"{page_idx}_{block_idx}_{line_idx}",
            source_text,
            source_text.strip(),
            f"{page_idx}_{block_idx}",
        ]
        for key in candidate_keys:
            if key and key in translations:
                return translations[key]
        return None

    @staticmethod
    def _format_detected_type(
        file_type: FileType,
        detection_type: Optional[DetectionType],
    ) -> str:
        if file_type == FileType.PDF:
            return "digital_pdf" if detection_type == DetectionType.DIGITAL else "scanned_pdf"
        if file_type == FileType.DOCX:
            return "docx"
        if file_type in (FileType.JPG, FileType.PNG):
            return "scanned_image"
        return str(file_type.value) if file_type else "unknown"

    def get_structure_path(self, job_id: str) -> str:
        """Path to the per-job structure.json."""
        return os.path.join(self.file_service.get_job_dir(job_id), "structure.json")

    @staticmethod
    def _json_safe_value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return {
                str(key): PipelineService._json_safe_value(item)
                for key, item in value.items()
                if key not in {"image", "mask"}
            }
        if isinstance(value, (list, tuple)):
            return [PipelineService._json_safe_value(item) for item in value]
        return str(value)

    def _block_metadata(self, block: Dict[str, Any]) -> Dict[str, Any]:
        metadata = {}
        for key, value in block.items():
            if key in {"type", "bbox", "lines", "image", "mask"}:
                continue
            metadata[key] = self._json_safe_value(value)
        return metadata

    def _structure_lines(self, block: Dict[str, Any]) -> List[Dict[str, Any]]:
        lines_out: List[Dict[str, Any]] = []
        for idx, line in enumerate(block.get("lines", []), start=1):
            text = (line.get("text") or "").strip()
            if not text:
                continue
            line_out = {
                "line_id": idx,
                "text": text,
                "source_text": text,
                "translated_text": None,
                "translation_method": None,
                "cascade_stage": None,
                "translation_confidence": None,
                "bbox": self._json_safe_value(line.get("bbox") or []),
                "font": line.get("font", ""),
                "size": line.get("size"),
                "color": self._json_safe_value(line.get("color")),
            }
            if line.get("confidence") is not None:
                line_out["ocr_confidence"] = line.get("confidence")
            if line.get("spans"):
                line_out["spans"] = self._json_safe_value(line.get("spans"))
            lines_out.append(line_out)
        return lines_out

    def _append_structure_warnings(self, job_id: str, warnings: List[str]) -> None:
        """Append reconstruction/layout warnings to structure.json."""
        if not warnings:
            return
        structure_path = self.get_structure_path(job_id)
        if not os.path.exists(structure_path):
            return
        try:
            with open(structure_path, "r", encoding="utf-8") as f:
                structure = json.load(f)

            existing = structure.get("warnings", [])
            if not isinstance(existing, list):
                existing = []
            for warning in warnings:
                if warning not in existing:
                    existing.append(warning)
            structure["warnings"] = existing

            with open(structure_path, "w", encoding="utf-8") as f:
                json.dump(structure, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Pipeline] Failed to append layout warnings to structure.json: {e}")

    def _create_output_pdf(
        self,
        layout_data: list,
        translations: Dict[str, Dict[str, str]],
        output_path: str,
        input_file_path: Optional[str] = None,
        file_type: Optional[FileType] = None,
        layout_warnings: Optional[List[str]] = None,
    ) -> bool:
        """
        Create a simple PDF from layout data with translated text.

        For image inputs, the original raster image is placed as the page
        background before translated OCR text is overlaid.
        """
        try:
            import fitz
            doc = fitz.open()
            page_items = layout_data or [{
                "page": 0,
                "width": 612,
                "height": 792,
                "blocks": [],
            }]

            for page_idx, page_data in enumerate(page_items):
                try:
                    page_width = float(page_data.get("width") or 612)
                    page_height = float(page_data.get("height") or 792)
                except Exception:
                    page_width = 612
                    page_height = 792

                page = doc.new_page(width=page_width, height=page_height)

                if (
                    page_idx == 0
                    and input_file_path
                    and file_type in (FileType.JPG, FileType.PNG)
                    and os.path.exists(input_file_path)
                ):
                    try:
                        page.insert_image(
                            page.rect,
                            filename=input_file_path,
                            keep_proportion=False,
                            overlay=False,
                        )
                    except Exception as e:
                        layout_warnings = layout_warnings if layout_warnings is not None else []
                        if f"Page {page_idx + 1}: failed to preserve source image background: {e}" not in layout_warnings:
                            layout_warnings.append(
                                f"Page {page_idx + 1}: failed to preserve source image background: {e}"
                            )

                blocks = page_data.get("blocks", [])
                
                for block_idx, block in enumerate(blocks, start=1):
                    if block.get("type") != "text":
                        continue
                    
                    lines = block.get("lines", [])
                    
                    for line_idx, line in enumerate(lines, start=1):
                        original = (line.get("text") or "").strip()
                        line_key = f"{page_idx}_{block_idx - 1}_{line_idx - 1}"
                        block_id = f"{page_idx}_{block_idx - 1}"
                        translated = (
                            translations.get(line_key, {}).get("translated")
                            or translations.get(block_id, {}).get("translated")
                            or translations.get(original, {}).get("translated")
                            or original
                        )
                        
                        if not translated or translated.strip() == original:
                            continue

                        rect = self.reconstruction_service._rect_from_bbox(
                            line.get("bbox"),
                            page.rect,
                            padding=0.75,
                        )
                        if rect is None:
                            if layout_warnings is not None:
                                layout_warnings.append(
                                    f"Page {page_idx + 1}, block {block_idx}, line {line_idx}: "
                                    "invalid text bbox; translated text was skipped."
                                )
                            continue

                        self.reconstruction_service._insert_text_in_rect(
                            page,
                            rect,
                            translated,
                            line,
                            layout_warnings,
                            page_idx + 1,
                            block_idx,
                            line_idx,
                            fallback_text=original or self.reconstruction_service.FALLBACK_REVIEW_TEXT,
                        )
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            doc.save(output_path)
            doc.close()
            return True
        
        except Exception as e:
            print(f"[Pipeline] Error creating output PDF: {e}")
            return False

    def _build_bilingual_first_page(
        self,
        layout_data: list,
        translations: Dict[str, Dict[str, str]],
    ) -> Dict[str, Any]:
        blocks_output = []
        if not layout_data:
            return {"blocks": blocks_output}

        first_page_blocks = layout_data[0].get("blocks", [])
        for block_index, block in enumerate(first_page_blocks):
            if block.get("type") != "text":
                continue

            block_bbox = block.get("bbox")
            for line in block.get("lines", []):
                original_text = line.get("text", "").strip()
                if not original_text:
                    continue

                block_id = f"0_{block_index}"
                translated_text = (
                    line.get("translation")
                    or translations.get(block_id, {}).get("translated")
                    or "UNKNOWN_FOR_REVIEW"
                )
                blocks_output.append(
                    {
                        "original_text": original_text,
                        "translated_text": translated_text,
                        "bbox": line.get("bbox") or block_bbox,
                    }
                )

        return {"blocks": blocks_output}
    
    def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        """Get status of a job"""
        return self.jobs.get(job_id)
    
    def get_job_output(self, job_id: str) -> Optional[str]:
        """Get output PDF path for a job"""
        job = self.jobs.get(job_id)
        if job and job.status == "completed":
            return self.file_service.get_output_path(job_id, "translated.pdf")
        return None

    def get_active_job_ids(self) -> List[str]:
        """Return job IDs that should not have their files cleaned up yet."""
        return [
            job_id
            for job_id, job in self.jobs.items()
            if job.status in {"queued", "processing"}
        ]

    def cleanup_job_files(self, job_id: str) -> bool:
        """Clean up one inactive job's files with active-job protection."""
        return self.file_service.cleanup_job(
            job_id,
            active_job_ids=self.get_active_job_ids(),
        )

    def cleanup_old_job_files(self, max_age_seconds: float) -> Dict[str, List[str]]:
        """Clean up old inactive job directories with active-job protection."""
        return self.file_service.cleanup_old_jobs(
            max_age_seconds,
            active_job_ids=self.get_active_job_ids(),
        )


# Global instance
_pipeline_service = None


def get_pipeline_service() -> PipelineService:
    """Get or create the global pipeline service"""
    global _pipeline_service
    if _pipeline_service is None:
        _pipeline_service = PipelineService()
    return _pipeline_service
