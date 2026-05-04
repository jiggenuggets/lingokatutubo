"""
Focused tests for layout extraction, PDF reconstruction, previews, and structure JSON.

Run from the backend directory:
    python -m unittest test_layout_backend.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))

from extraction_service import ExtractionService
from models import DetectionType, FileType
from pipeline_service import JobStatus, PipelineService
from reconstruction_service import ReconstructionService


class FakeFileService:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir

    def get_job_dir(self, job_id: str) -> str:
        return os.path.join(self.root_dir, job_id)


class FakeLanguageService:
    def detect_language(self, _text: str) -> dict:
        return {"language": "english"}


class AvailableOCR:
    def is_available(self) -> bool:
        return True


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


if __name__ == "__main__":
    unittest.main()
