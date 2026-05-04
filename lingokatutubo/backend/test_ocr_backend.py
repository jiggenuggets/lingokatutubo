"""
Small automated tests for OCR language resolution and structure.json warnings.

Run from the backend directory:
    python -m unittest test_ocr_backend.py
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

# Ensure backend modules import when this file is run directly or by unittest.
sys.path.insert(0, os.path.dirname(__file__))

from models import DetectionType, FileType
from ocr_stage.ocr_service import OCRService
from pipeline_service import JobStatus, PipelineService


class FakeOCRService(OCRService):
    """OCRService with a fake installed-language list."""

    def __init__(self, installed_languages):
        super().__init__(detect_orientation=False)
        self._installed_languages = list(installed_languages)

    def _ensure_available(self) -> None:
        self._verified = True


class FakeFileService:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir

    def get_job_dir(self, job_id: str) -> str:
        return os.path.join(self.root_dir, job_id)

    def get_output_path(self, job_id: str, filename: str) -> str:
        job_dir = self.get_job_dir(job_id)
        os.makedirs(job_dir, exist_ok=True)
        return os.path.join(job_dir, filename)


class FakeLanguageService:
    def detect_language(self, _text: str) -> dict:
        return {"language": "english"}


class AvailableOCR:
    def is_available(self) -> bool:
        return True


class BlankOCR(AvailableOCR):
    def extract_pdf_text_and_layout(self, _pdf_path: str, languages=None):
        return [{
            "page": 0,
            "width": 612,
            "height": 792,
            "blocks": [],
        }]


class ScannedDetectionService:
    def detect_pdf_type(self, _pdf_path: str) -> DetectionType:
        return DetectionType.SCANNED


class OCRLanguageResolutionTests(unittest.TestCase):
    def test_english_resolves_to_eng(self):
        lang, warnings = FakeOCRService(["eng", "osd"]).resolve_tesseract_language("english")

        self.assertEqual(lang, "eng")
        self.assertEqual(warnings, [])

    def test_filipino_prefers_fil_then_tgl_then_eng_warning(self):
        lang, warnings = FakeOCRService(["eng", "fil", "tgl"]).resolve_tesseract_language("filipino")
        self.assertEqual(lang, "fil")
        self.assertEqual(warnings, [])

        lang, warnings = FakeOCRService(["eng", "tgl"]).resolve_tesseract_language("filipino")
        self.assertEqual(lang, "tgl")
        self.assertEqual(warnings, [])

        lang, warnings = FakeOCRService(["eng"]).resolve_tesseract_language("filipino")
        self.assertEqual(lang, "eng")
        self.assertTrue(any("fil or tgl" in warning for warning in warnings))

    def test_tagalog_resolves_to_tgl_or_eng_warning(self):
        lang, warnings = FakeOCRService(["eng", "tgl"]).resolve_tesseract_language("tagalog")
        self.assertEqual(lang, "tgl")
        self.assertEqual(warnings, [])

        lang, warnings = FakeOCRService(["eng"]).resolve_tesseract_language("tagalog")
        self.assertEqual(lang, "eng")
        self.assertTrue(any("tgl" in warning for warning in warnings))

    def test_cebuano_resolves_to_ceb_or_eng_warning(self):
        lang, warnings = FakeOCRService(["eng", "ceb"]).resolve_tesseract_language("cebuano")
        self.assertEqual(lang, "ceb")
        self.assertEqual(warnings, [])

        lang, warnings = FakeOCRService(["eng"]).resolve_tesseract_language("cebuano")
        self.assertEqual(lang, "eng")
        self.assertTrue(any("ceb" in warning for warning in warnings))

    def test_bagobo_tagabawa_resolves_to_eng_warning(self):
        lang, warnings = FakeOCRService(["eng"]).resolve_tesseract_language("bagobo/tagabawa")

        self.assertEqual(lang, "eng")
        self.assertTrue(any("Bagobo/Tagabawa" in warning for warning in warnings))

    def test_missing_requested_language_pack_does_not_crash(self):
        lang, warnings = FakeOCRService(["eng"]).resolve_tesseract_language(["english", "cebuano"])

        self.assertEqual(lang, "eng")
        self.assertTrue(any("cebuano" in warning and "ceb" in warning for warning in warnings))


class StructureWarningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_structure_service(self, ocr_service=None) -> PipelineService:
        service = PipelineService.__new__(PipelineService)
        service.file_service = FakeFileService(self.tmp.name)
        service.language_service = FakeLanguageService()
        service.ocr_service = ocr_service or AvailableOCR()
        service.jobs = {}
        return service

    @staticmethod
    def read_json(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def write_blank_pdf(path: Path) -> None:
        doc = fitz.open()
        doc.new_page(width=612, height=792)
        doc.save(path)
        doc.close()

    def test_warning_is_saved_into_structure_json(self):
        service = self.make_structure_service()
        job = JobStatus("warning-job")
        job.status = "processing"
        job.detection_type = DetectionType.SCANNED
        layout_data = [{
            "page": 0,
            "width": 612,
            "height": 792,
            "ocr_warning": "Requested OCR language 'cebuano' requires Tesseract pack(s) ceb",
            "blocks": [{
                "type": "text",
                "bbox": [10, 20, 200, 60],
                "confidence": 0.91,
                "lines": [{"text": "Hello OCR", "bbox": [10, 20, 200, 60]}],
            }],
        }]

        path = service._build_and_save_structure(
            job_id=job.job_id,
            job=job,
            file_type=FileType.PDF,
            layout_data=layout_data,
            input_file_path="unused.pdf",
        )
        structure = self.read_json(path)

        self.assertTrue(any("Page 1: Requested OCR language" in w for w in structure["warnings"]))

    def test_digital_pdf_ocr_confidence_is_null(self):
        service = self.make_structure_service()
        job = JobStatus("digital-job")
        job.status = "processing"
        job.detection_type = DetectionType.DIGITAL
        layout_data = [{
            "page": 0,
            "width": 612,
            "height": 792,
            "blocks": [{
                "type": "text",
                "bbox": [10, 20, 200, 60],
                "confidence": 0.99,
                "lines": [{"text": "Digital text", "bbox": [10, 20, 200, 60]}],
            }],
        }]

        path = service._build_and_save_structure(
            job_id=job.job_id,
            job=job,
            file_type=FileType.PDF,
            layout_data=layout_data,
            input_file_path="unused.pdf",
        )
        block = self.read_json(path)["pages"][0]["blocks"][0]

        self.assertIsNone(block["ocr_confidence"])

    def test_scanned_ocr_confidence_is_saved_when_text_is_extracted(self):
        service = self.make_structure_service()
        job = JobStatus("scanned-job")
        job.status = "processing"
        job.detection_type = DetectionType.SCANNED
        layout_data = [{
            "page": 0,
            "width": 612,
            "height": 792,
            "blocks": [{
                "type": "text",
                "bbox": [10, 20, 200, 60],
                "confidence": 0.87654,
                "lines": [{"text": "Scanned text", "bbox": [10, 20, 200, 60]}],
            }],
        }]

        path = service._build_and_save_structure(
            job_id=job.job_id,
            job=job,
            file_type=FileType.PDF,
            layout_data=layout_data,
            input_file_path="unused.pdf",
        )
        block = self.read_json(path)["pages"][0]["blocks"][0]

        self.assertEqual(block["ocr_confidence"], 0.8765)

    def test_blank_scanned_pdf_fails_and_structure_endpoint_returns_warnings(self):
        service = self.make_structure_service(ocr_service=BlankOCR())
        service.detection_service = ScannedDetectionService()
        service.extraction_service = object()
        service.reconstruction_service = object()
        service.translation_dataset = object()

        pdf_path = Path(self.tmp.name) / "blank.pdf"
        self.write_blank_pdf(pdf_path)
        job_id = f"blank-{uuid.uuid4().hex[:8]}"

        ok = asyncio.run(
            service.process_translation(
                job_id=job_id,
                input_file_path=str(pdf_path),
                file_type=FileType.PDF,
                source_language="english",
                target_language="tagabawa",
            )
        )

        self.assertFalse(ok)
        self.assertEqual(service.jobs[job_id].status, "failed")
        self.assertIn("OCR produced no text", service.jobs[job_id].error)

        import main as main_module

        original_pipeline_service = main_module.pipeline_service
        main_module.pipeline_service = service
        try:
            response = TestClient(main_module.app).get(f"/structure/{job_id}")
        finally:
            main_module.pipeline_service = original_pipeline_service

        self.assertEqual(response.status_code, 200)
        structure = response.json()
        self.assertEqual(structure["status"], "failed")
        self.assertTrue(any("OCR did not extract any text" in w for w in structure["warnings"]))


if __name__ == "__main__":
    unittest.main()
