"""
Translation pipeline coordinator
Orchestrates the entire translation workflow
"""

import asyncio
import os
from typing import Optional, Dict, Any, List, Sequence, Union
from datetime import datetime
import json

from models import FileType, DetectionType
from file_service import get_file_service
from detection_service import get_detection_service
from extraction_service import get_extraction_service
from reconstruction_service import get_reconstruction_service
from translation_dataset import get_translation_dataset
from language_detection_service import get_language_detection_service
from ocr_stage import OCRUnavailableError, get_ocr_service


class JobStatus:
    """Tracks the status of a translation job"""
    
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.status = "queued"  # queued, processing, completed, failed
        self.progress = 0
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

            print(f"[Pipeline] Job {job_id} started")

            # Phase 1: Detect file type (digital vs scanned)
            print(f"[Pipeline] Phase 1: Detecting file type for {job_id}")
            job.progress = 10
            
            if file_type == FileType.PDF:
                job.detection_type = self.detection_service.detect_pdf_type(input_file_path)
            elif file_type == FileType.DOCX:
                job.detection_type = self.detection_service.detect_docx_type(input_file_path)
            else:  # JPG, PNG
                job.detection_type = self.detection_service.detect_image_type(input_file_path)
            
            print(f"[Pipeline] Detected: {job.detection_type}")
            
            # Phase 2: Extract text and layout
            print(f"[Pipeline] Phase 2: Extracting text and layout")
            job.progress = 25
            
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
            job.progress = 50

            translations = self._translate_layout(layout_data, source_language, target_language)

            # Debug: log first 10 translation pairs to backend console
            _sample = list(translations.items())[:10]
            for _orig, _xlat in _sample:
                print(f'[Pipeline] Translated: "{_orig}" -> "{_xlat}"')

            job.metadata["translated_blocks"] = len(translations)
            
            # Phase 4: Reconstruct document
            print(f"[Pipeline] Phase 4: Reconstructing document")
            job.progress = 75
            
            output_pdf_path = self.file_service.get_output_path(job_id, "translated.pdf")
            
            if file_type == FileType.PDF:
                success = self.reconstruction_service.reconstruct_pdf(
                    input_file_path,
                    layout_data,
                    translations,
                    output_pdf_path
                )
            else:
                # For DOCX/images, create a simple PDF output
                success = self._create_output_pdf(
                    layout_data,
                    translations,
                    output_pdf_path
                )
            
            if not success:
                raise Exception("Failed to reconstruct PDF")

            if not os.path.exists(output_pdf_path):
                raise Exception(f"Output PDF was not created at: {output_pdf_path}")

            print(f"[Pipeline] Output PDF created: {output_pdf_path} ({os.path.getsize(output_pdf_path)} bytes)")

            # Phase 5: Create bilingual preview
            print(f"[Pipeline] Phase 5: Creating preview")
            job.progress = 85
            
            preview_dir = os.path.join(self.file_service.get_job_dir(job_id), "preview")
            original_previews = self.reconstruction_service.create_preview_images(
                input_file_path, preview_dir, max_pages=2
            )
            translated_previews = self.reconstruction_service.create_preview_images(
                output_pdf_path, preview_dir, max_pages=2
            )
            
            job.metadata["preview_original"] = original_previews
            job.metadata["preview_translated"] = translated_previews
            
            # Phase 6: Create bilingual PDF
            print(f"[Pipeline] Phase 6: Creating bilingual PDF")
            job.progress = 95
            
            bilingual_path = self.file_service.get_output_path(job_id, "bilingual.pdf")
            if file_type == FileType.PDF:
                self.reconstruction_service.create_bilingual_pdf(
                    input_file_path,
                    output_pdf_path,
                    bilingual_path
                )
                job.metadata["bilingual_pdf"] = bilingual_path
            
            job.status = "completed"
            job.progress = 100
            job.completed_at = datetime.now()
            
            print(f"[Pipeline] Job {job_id} completed successfully")
            return True
        
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            print(f"[Pipeline] Job {job_id} failed: {e}")
            return False
    
    def _translate_layout(
        self,
        layout_data: list,
        source_lang: str,
        target_lang: str
    ) -> Dict[str, str]:
        """
        Translate all text in layout data
        
        Returns:
            Dict mapping original text -> translated text
        """
        translations = {}
        
        for page_data in layout_data:
            blocks = page_data.get("blocks", [])
            
            for block in blocks:
                if block.get("type") != "text":
                    continue
                
                lines = block.get("lines", [])
                
                for line in lines:
                    original = line.get("text", "").strip()
                    if not original:
                        continue
                    
                    # Translate using dataset
                    translated = self.translation_dataset.translate_phrase(
                        original,
                        source_lang=source_lang,
                        target_lang=target_lang
                    )
                    
                    translations[original] = translated
        
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

        total_blocks = 0
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
                if block.get("type") != "text":
                    continue
                block_counter += 1
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
                    "bbox": list(bbox),
                    "source_text": text,
                    "detected_language": detected_lang,
                    "ocr_confidence": ocr_conf,
                })
                total_blocks += 1

            pages_out.append({
                "page_number": page_idx + 1,
                "width": page_data.get("width"),
                "height": page_data.get("height"),
                "blocks": blocks_out,
            })

        # If scanned and OCR found nothing, still surface page geometry so
        # the frontend can render an empty preview, and add a clear warning.
        if is_scanned and total_blocks == 0:
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

    def _create_output_pdf(
        self,
        layout_data: list,
        translations: Dict[str, str],
        output_path: str
    ) -> bool:
        """
        Create a simple PDF from layout data with translated text
        """
        try:
            import fitz
            doc = fitz.open()
            page = doc.new_page()
            
            y_pos = 50
            for page_data in layout_data:
                blocks = page_data.get("blocks", [])
                
                for block in blocks:
                    if block.get("type") != "text":
                        continue
                    
                    lines = block.get("lines", [])
                    
                    for line in lines:
                        original = line.get("text", "")
                        translated = translations.get(original, original)
                        
                        if translated:
                            page.insert_text((50, y_pos), translated, fontsize=11)
                            y_pos += 20
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            doc.save(output_path)
            doc.close()
            return True
        
        except Exception as e:
            print(f"[Pipeline] Error creating output PDF: {e}")
            return False
    
    def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        """Get status of a job"""
        return self.jobs.get(job_id)
    
    def get_job_output(self, job_id: str) -> Optional[str]:
        """Get output PDF path for a job"""
        job = self.jobs.get(job_id)
        if job and job.status == "completed":
            return self.file_service.get_output_path(job_id, "translated.pdf")
        return None


# Global instance
_pipeline_service = None


def get_pipeline_service() -> PipelineService:
    """Get or create the global pipeline service"""
    global _pipeline_service
    if _pipeline_service is None:
        _pipeline_service = PipelineService()
    return _pipeline_service
