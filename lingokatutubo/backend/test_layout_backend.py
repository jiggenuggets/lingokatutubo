"""
Focused tests for layout extraction, PDF reconstruction, previews, and structure JSON.

Run from the backend directory:
    python -m unittest test_layout_backend.py
"""

import json
import asyncio
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))

from detection_service import get_detection_service
from extraction_service import ExtractionService
from models import DetectionType, FileType
from pipeline_service import JobStatus, PipelineService
from reconstruction_service import ReconstructionService


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


class FakeTranslationDataset:
    is_loaded = True

    def translate_phrase(self, text: str, source_lang: str = "english", target_lang: str = "tagabawa") -> str:
        translations = {
            "Hello": "This translated phrase is deliberately much longer than the original box",
            "body": "lawa",
            "water": "wayig",
        }
        return translations.get(text.strip(), text)


class FakeOCR(AvailableOCR):
    """Deterministic OCR layout for scanned-PDF and direct-image pipeline tests."""

    @staticmethod
    def _page_layout(page_idx: int, width: float, height: float, text: str = "Hello") -> dict:
        return {
            "page": page_idx,
            "width": float(width),
            "height": float(height),
            "blocks": [{
                "type": "text",
                "bbox": [30, 35, 70, 55],
                "confidence": 0.96,
                "lines": [{
                    "text": text,
                    "bbox": [30, 35, 70, 55],
                    "font": "",
                    "size": 12,
                    "confidence": 0.96,
                }],
            }],
        }

    def extract_pdf_text_and_layout(self, pdf_path: str, languages=None):
        doc = fitz.open(pdf_path)
        try:
            return [
                self._page_layout(i, doc[i].rect.width, doc[i].rect.height)
                for i in range(doc.page_count)
            ]
        finally:
            doc.close()

    def extract_image_text_and_layout(self, image_path: str, languages=None):
        image = Image.open(image_path)
        try:
            # Match the real OCR service's points-at-200-DPI convention.
            width = image.width * 72.0 / 200.0
            height = image.height * 72.0 / 200.0
            return [self._page_layout(0, width, height)]
        finally:
            image.close()


class BlankOCR(AvailableOCR):
    def extract_pdf_text_and_layout(self, pdf_path: str, languages=None):
        doc = fitz.open(pdf_path)
        try:
            return [
                {
                    "page": i,
                    "width": float(doc[i].rect.width),
                    "height": float(doc[i].rect.height),
                    "blocks": [],
                }
                for i in range(doc.page_count)
            ]
        finally:
            doc.close()


class RegressionDetectionService:
    def __init__(self):
        self._real = get_detection_service()

    def detect_pdf_type(self, pdf_path: str) -> DetectionType:
        return self._real.detect_pdf_type(pdf_path)

    def detect_docx_type(self, docx_path: str) -> DetectionType:
        return self._real.detect_docx_type(docx_path)

    def detect_image_type(self, image_path: str) -> DetectionType:
        return DetectionType.SCANNED


class LayoutExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_pdf_extraction_captures_image_drawing_and_text_style(self):
        image_path = self.tmp_path / "sample.png"
        Image.new("RGB", (24, 24), (40, 90, 180)).save(image_path)

        pdf_path = self.tmp_path / "layout.pdf"
        doc = fitz.open()
        page = doc.new_page(width=240, height=180)
        page.insert_image(fitz.Rect(20, 20, 80, 80), filename=str(image_path))
        page.draw_rect(fitz.Rect(100, 20, 180, 80), color=(0, 0, 0), width=1)
        page.insert_text((20, 130), "Hello layout", fontsize=12, color=(0.1, 0.2, 0.3))
        doc.save(pdf_path)
        doc.close()

        layout = ExtractionService.extract_pdf_text_and_layout(str(pdf_path))
        blocks = layout[0]["blocks"]
        block_types = {block["type"] for block in blocks}

        self.assertIn("text", block_types)
        self.assertIn("image", block_types)
        self.assertIn("drawing", block_types)

        text_block = next(block for block in blocks if block["type"] == "text")
        line = text_block["lines"][0]
        self.assertIn("size", line)
        self.assertIn("color", line)
        self.assertTrue(line["spans"])


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    @staticmethod
    def _write_base_pdf(path: Path) -> None:
        doc = fitz.open()
        page = doc.new_page(width=320, height=220)
        page.insert_text((40, 70), "Hello", fontsize=12)
        doc.save(path)
        doc.close()

    def test_reconstruction_preserves_page_size_and_records_overflow_warning(self):
        input_pdf = self.tmp_path / "input.pdf"
        output_pdf = self.tmp_path / "translated.pdf"
        self._write_base_pdf(input_pdf)

        layout_data = [{
            "page": 0,
            "width": 320,
            "height": 220,
            "blocks": [{
                "type": "text",
                "bbox": [40, 58, 75, 73],
                "lines": [{
                    "text": "Hello",
                    "bbox": [40, 58, 75, 73],
                    "font": "Helvetica",
                    "size": 12,
                    "color": [0, 0, 0],
                }],
            }],
        }]
        warnings = []

        ok = ReconstructionService.reconstruct_pdf(
            str(input_pdf),
            layout_data,
            {
                "Hello": (
                    "This translated phrase is deliberately much longer "
                    "than the source box"
                )
            },
            str(output_pdf),
            layout_warnings=warnings,
        )

        self.assertTrue(ok)
        self.assertTrue(output_pdf.exists())
        self.assertTrue(any("too long for its bbox" in warning for warning in warnings))

        out_doc = fitz.open(output_pdf)
        self.assertEqual(round(out_doc[0].rect.width), 320)
        self.assertEqual(round(out_doc[0].rect.height), 220)
        out_doc.close()

    def test_preview_prefixes_prevent_original_translated_overwrite(self):
        input_pdf = self.tmp_path / "input.pdf"
        output_pdf = self.tmp_path / "translated.pdf"
        preview_dir = self.tmp_path / "preview"
        self._write_base_pdf(input_pdf)
        self._write_base_pdf(output_pdf)

        original = ReconstructionService.create_preview_images(
            str(input_pdf),
            str(preview_dir),
            max_pages=1,
            prefix="original",
        )
        translated = ReconstructionService.create_preview_images(
            str(output_pdf),
            str(preview_dir),
            max_pages=1,
            prefix="translated",
        )

        self.assertEqual(Path(original[0]).name, "original_page_0.png")
        self.assertEqual(Path(translated[0]).name, "translated_page_0.png")
        self.assertNotEqual(original[0], translated[0])
        self.assertTrue(Path(original[0]).exists())
        self.assertTrue(Path(translated[0]).exists())

    def test_unicode_translation_uses_embedded_font_when_available(self):
        if not ReconstructionService._unicode_fontfile():
            self.skipTest("No Unicode system font available for PDF embedding")

        input_pdf = self.tmp_path / "input.pdf"
        output_pdf = self.tmp_path / "translated-unicode.pdf"
        self._write_base_pdf(input_pdf)
        layout_data = [{
            "page": 0,
            "width": 320,
            "height": 220,
            "blocks": [{
                "type": "text",
                "bbox": [40, 58, 150, 74],
                "lines": [{
                    "text": "Hello",
                    "bbox": [40, 58, 150, 74],
                    "font": "Helvetica",
                    "size": 12,
                    "color": [0, 0, 0],
                }],
            }],
        }]

        ok = ReconstructionService.reconstruct_pdf(
            str(input_pdf),
            layout_data,
            {"Hello": "kann\u0119"},
            str(output_pdf),
            layout_warnings=[],
        )

        self.assertTrue(ok)
        out_doc = fitz.open(output_pdf)
        extracted = out_doc[0].get_text()
        out_doc.close()
        self.assertIn("kann\u0119", extracted)


class StructureJsonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_structure_service(self) -> PipelineService:
        service = PipelineService.__new__(PipelineService)
        service.file_service = FakeFileService(self.tmp.name)
        service.language_service = FakeLanguageService()
        service.ocr_service = AvailableOCR()
        service.jobs = {}
        return service

    @staticmethod
    def read_json(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_structure_json_keeps_text_lines_and_non_text_geometry(self):
        service = self.make_structure_service()
        job = JobStatus("layout-job")
        job.status = "processing"
        job.detection_type = DetectionType.DIGITAL
        layout_data = [{
            "page": 0,
            "width": 240,
            "height": 180,
            "rotation": 0,
            "blocks": [
                {
                    "type": "text",
                    "bbox": [10, 20, 100, 40],
                    "lines": [{
                        "text": "Hello layout",
                        "bbox": [10, 20, 100, 40],
                        "font": "Helvetica",
                        "size": 12,
                        "color": [0, 0, 0],
                    }],
                },
                {
                    "type": "image",
                    "bbox": [10, 50, 80, 120],
                    "width": 70,
                    "height": 70,
                    "ext": "png",
                },
                {
                    "type": "drawing",
                    "bbox": [90, 50, 180, 120],
                    "drawing_type": "s",
                    "items_count": 4,
                },
            ],
        }]

        path = service._build_and_save_structure(
            job_id=job.job_id,
            job=job,
            file_type=FileType.PDF,
            layout_data=layout_data,
            input_file_path="unused.pdf",
        )
        service._append_structure_warnings(job.job_id, ["layout warning saved"])
        structure = self.read_json(path)

        blocks = structure["pages"][0]["blocks"]
        self.assertEqual([block["block_type"] for block in blocks], ["text", "image", "drawing"])
        self.assertEqual(blocks[0]["lines"][0]["bbox"], [10, 20, 100, 40])
        self.assertEqual(blocks[1]["metadata"]["ext"], "png")
        self.assertIn("layout warning saved", structure["warnings"])


class PipelineLayoutRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def make_pipeline_service(self, ocr_service=None) -> PipelineService:
        service = PipelineService.__new__(PipelineService)
        service.file_service = FakeFileService(self.tmp.name)
        service.detection_service = RegressionDetectionService()
        service.extraction_service = ExtractionService()
        service.reconstruction_service = ReconstructionService()
        service.translation_dataset = FakeTranslationDataset()
        service.language_service = FakeLanguageService()
        service.ocr_service = ocr_service or FakeOCR()
        service.jobs = {}
        return service

    @staticmethod
    def read_json(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def write_text_pdf(path: Path, width: float = 320, height: float = 220) -> None:
        doc = fitz.open()
        page = doc.new_page(width=width, height=height)
        page.insert_text((40, 80), "Hello", fontsize=12)
        doc.save(path)
        doc.close()

    @staticmethod
    def write_sample_image(path: Path, width: int = 160, height: int = 100) -> None:
        image = Image.new("RGB", (width, height), (245, 248, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle([10, 10, width - 10, height - 10], outline=(30, 70, 140), width=3)
        draw.text((24, height // 2 - 8), "Hello", fill=(0, 0, 0))
        image.save(path)

    def write_pdf_with_image(self, path: Path, width: float = 360, height: float = 240) -> None:
        image_path = self.tmp_path / "embedded.png"
        self.write_sample_image(image_path, 80, 60)

        doc = fitz.open()
        page = doc.new_page(width=width, height=height)
        page.insert_text((36, 48), "Hello", fontsize=12)
        page.insert_image(fitz.Rect(36, 75, 140, 155), filename=str(image_path))
        doc.save(path)
        doc.close()

    @staticmethod
    def write_table_like_pdf(path: Path, width: float = 360, height: float = 240) -> None:
        doc = fitz.open()
        page = doc.new_page(width=width, height=height)
        page.insert_text((40, 45), "Hello", fontsize=12)
        for x in (60, 150, 240, 330):
            page.draw_line((x, 80), (x, 170), color=(0, 0, 0), width=0.8)
        for y in (80, 125, 170):
            page.draw_line((60, y), (330, y), color=(0, 0, 0), width=0.8)
        page.insert_text((75, 110), "body", fontsize=10)
        page.insert_text((165, 110), "water", fontsize=10)
        doc.save(path)
        doc.close()

    def write_scanned_pdf(self, path: Path, width: float = 360, height: float = 240) -> None:
        image_path = self.tmp_path / "scan-page.png"
        self.write_sample_image(image_path, 600, 400)

        doc = fitz.open()
        page = doc.new_page(width=width, height=height)
        page.insert_image(page.rect, filename=str(image_path))
        doc.save(path)
        doc.close()

    @staticmethod
    def assert_page_size(testcase: unittest.TestCase, page: dict, width: float, height: float) -> None:
        testcase.assertAlmostEqual(float(page["width"]), width, delta=0.75)
        testcase.assertAlmostEqual(float(page["height"]), height, delta=0.75)

    @staticmethod
    def block_types(structure: dict) -> list:
        return [
            block.get("block_type")
            for page in structure.get("pages", [])
            for block in page.get("blocks", [])
        ]

    def run_pipeline(self, service: PipelineService, path: Path, file_type: FileType) -> str:
        job_id = f"layout-{uuid.uuid4().hex[:8]}"
        ok = asyncio.run(
            service.process_translation(
                job_id=job_id,
                input_file_path=str(path),
                file_type=file_type,
                source_language="english",
                target_language="tagabawa",
            )
        )
        self.assertTrue(ok, service.jobs[job_id].error)
        self.assertEqual(service.jobs[job_id].status, "completed")
        return job_id

    def endpoint_json(self, service: PipelineService, job_id: str, expect_download: bool = True) -> dict:
        import main as main_module

        original_pipeline_service = main_module.pipeline_service
        main_module.pipeline_service = service
        try:
            client = TestClient(main_module.app)
            structure_response = client.get(f"/structure/{job_id}")
            self.assertEqual(structure_response.status_code, 200)
            if expect_download:
                download_response = client.get(f"/download/{job_id}")
                self.assertEqual(download_response.status_code, 200)
                self.assertEqual(download_response.headers.get("content-type"), "application/pdf")
            return structure_response.json()
        finally:
            main_module.pipeline_service = original_pipeline_service

    def assert_completed_outputs(self, service: PipelineService, job_id: str) -> None:
        output_path = service.get_job_output(job_id)
        self.assertTrue(output_path and os.path.exists(output_path))

        metadata = service.jobs[job_id].metadata
        original_previews = metadata.get("preview_original", [])
        translated_previews = metadata.get("preview_translated", [])
        self.assertTrue(original_previews)
        self.assertTrue(translated_previews)
        self.assertEqual(Path(original_previews[0]).name, "original_page_0.png")
        self.assertEqual(Path(translated_previews[0]).name, "translated_page_0.png")
        self.assertNotEqual(original_previews[0], translated_previews[0])
        self.assertTrue(os.path.exists(original_previews[0]))
        self.assertTrue(os.path.exists(translated_previews[0]))

    def assert_output_page_size(self, service: PipelineService, job_id: str, width: float, height: float) -> None:
        output_path = service.get_job_output(job_id)
        doc = fitz.open(output_path)
        try:
            self.assertAlmostEqual(doc[0].rect.width, width, delta=0.75)
            self.assertAlmostEqual(doc[0].rect.height, height, delta=0.75)
        finally:
            doc.close()

    def assert_output_has_image(self, service: PipelineService, job_id: str) -> None:
        output_path = service.get_job_output(job_id)
        doc = fitz.open(output_path)
        try:
            self.assertGreaterEqual(len(doc[0].get_images(full=True)), 1)
        finally:
            doc.close()

    def test_pipeline_digital_text_only_pdf_fixture(self):
        service = self.make_pipeline_service()
        pdf_path = self.tmp_path / "digital-text-only.pdf"
        self.write_text_pdf(pdf_path)

        job_id = self.run_pipeline(service, pdf_path, FileType.PDF)
        structure = self.endpoint_json(service, job_id)
        self.assert_completed_outputs(service, job_id)

        self.assertEqual(structure["detected_type"], "digital_pdf")
        self.assert_page_size(self, structure["pages"][0], 320, 220)
        self.assertIn("text", self.block_types(structure))
        self.assertNotIn("image", self.block_types(structure))
        self.assert_output_page_size(service, job_id, 320, 220)
        self.assertTrue(any("too long for its bbox" in warning for warning in structure["warnings"]))

    def test_pipeline_digital_pdf_with_image_fixture(self):
        service = self.make_pipeline_service()
        pdf_path = self.tmp_path / "digital-with-image.pdf"
        self.write_pdf_with_image(pdf_path)

        job_id = self.run_pipeline(service, pdf_path, FileType.PDF)
        structure = self.endpoint_json(service, job_id)
        self.assert_completed_outputs(service, job_id)

        self.assertEqual(structure["detected_type"], "digital_pdf")
        self.assert_page_size(self, structure["pages"][0], 360, 240)
        block_types = self.block_types(structure)
        self.assertIn("text", block_types)
        self.assertIn("image", block_types)
        self.assert_output_page_size(service, job_id, 360, 240)
        self.assert_output_has_image(service, job_id)

    def test_pipeline_digital_pdf_with_table_like_grid_fixture(self):
        service = self.make_pipeline_service()
        pdf_path = self.tmp_path / "digital-table-like.pdf"
        self.write_table_like_pdf(pdf_path)

        job_id = self.run_pipeline(service, pdf_path, FileType.PDF)
        structure = self.endpoint_json(service, job_id)
        self.assert_completed_outputs(service, job_id)

        self.assertEqual(structure["detected_type"], "digital_pdf")
        self.assert_page_size(self, structure["pages"][0], 360, 240)
        block_types = set(self.block_types(structure))
        self.assertIn("text", block_types)
        self.assertTrue({"drawing", "table"} & block_types)
        self.assert_output_page_size(service, job_id, 360, 240)

    def test_pipeline_scanned_image_only_pdf_fixture(self):
        service = self.make_pipeline_service(ocr_service=FakeOCR())
        pdf_path = self.tmp_path / "scanned-image-only.pdf"
        self.write_scanned_pdf(pdf_path)

        job_id = self.run_pipeline(service, pdf_path, FileType.PDF)
        structure = self.endpoint_json(service, job_id)
        self.assert_completed_outputs(service, job_id)

        self.assertEqual(structure["detected_type"], "scanned_pdf")
        self.assert_page_size(self, structure["pages"][0], 360, 240)
        self.assertIn("text", self.block_types(structure))
        self.assert_output_page_size(service, job_id, 360, 240)
        self.assert_output_has_image(service, job_id)

    def test_pipeline_direct_image_upload_fixture(self):
        service = self.make_pipeline_service(ocr_service=FakeOCR())
        image_path = self.tmp_path / "direct-image.png"
        self.write_sample_image(image_path, width=400, height=260)

        job_id = self.run_pipeline(service, image_path, FileType.PNG)
        structure = self.endpoint_json(service, job_id)
        self.assert_completed_outputs(service, job_id)

        expected_width = 400 * 72.0 / 200.0
        expected_height = 260 * 72.0 / 200.0
        self.assertEqual(structure["detected_type"], "scanned_image")
        self.assert_page_size(self, structure["pages"][0], expected_width, expected_height)
        self.assertIn("text", self.block_types(structure))
        self.assert_output_page_size(service, job_id, expected_width, expected_height)
        self.assert_output_has_image(service, job_id)

    def test_pipeline_blank_scanned_pdf_fails_honestly_with_structure(self):
        service = self.make_pipeline_service(ocr_service=BlankOCR())
        pdf_path = self.tmp_path / "blank-scanned.pdf"
        doc = fitz.open()
        doc.new_page(width=300, height=180)
        doc.save(pdf_path)
        doc.close()

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

        structure = self.endpoint_json(service, job_id, expect_download=False)
        self.assertEqual(structure["status"], "failed")
        self.assertEqual(structure["detected_type"], "scanned_pdf")
        self.assert_page_size(self, structure["pages"][0], 300, 180)
        self.assertEqual(structure["pages"][0]["blocks"], [])
        self.assertTrue(any("OCR did not extract any text" in warning for warning in structure["warnings"]))
        self.assertIsNone(service.get_job_output(job_id))


if __name__ == "__main__":
    unittest.main()
