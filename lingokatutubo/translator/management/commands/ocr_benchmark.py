"""
Management command: ocr_benchmark

Run OCR evaluation across a set of fixture images and PDFs,
compute CER/WER against ground-truth text files, benchmark PSM
modes 3/4/6, and write a QA report (JSON + CSV).

Usage:
    python manage.py ocr_benchmark
    python manage.py ocr_benchmark --fixture-dir /path/to/fixtures
    python manage.py ocr_benchmark --output-dir /path/to/reports
    python manage.py ocr_benchmark --psm 3 4 6
    python manage.py ocr_benchmark --lang eng
    python manage.py ocr_benchmark --check-only

When Tesseract is not installed the command prints the environment
report and exits cleanly with a non-zero exit code.  It never
silently pretends OCR worked when the engine is missing.
"""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.core.management.base import BaseCommand, CommandError


# ---------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------

GROUND_TRUTH: Dict[str, str] = {
    "clean_scan":    "Hello world. This is a clean scan document.",
    "faded_scan":    "Hello world. This is a clean scan document.",
    "rotated_90":    "Hello world. This is a clean scan document.",
    "rotated_180":   "Hello world. This is a clean scan document.",
    "rotated_270":   "Hello world. This is a clean scan document.",
    "two_column":    "Left column text. Right column text.",
    "table":         "Name Age City. Alice 30 Manila. Bob 25 Davao.",
    "blank":         "",
}


def build_fixtures(fixture_dir: Path) -> Dict[str, Path]:
    """Create synthetic fixture images + PDFs in fixture_dir.

    Returns a dict mapping fixture_name → Path.
    All images are generated with PIL so no external fonts are needed.

    Phase 7: images are now saved at 300 DPI with 4× scale (2400×800 px)
    so Tesseract sees text at ~44 px / 11 pt — legible without a TrueType
    font. DPI metadata is embedded so Tesseract does not fall back to 70 DPI.
    """
    from PIL import Image, ImageDraw

    _FIXTURE_DPI = 300
    _SCALE = 4  # 4× the original 600×200 → 2400×800

    image_dir = fixture_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    gt_dir = fixture_dir / "ground_truth"
    gt_dir.mkdir(parents=True, exist_ok=True)

    paths: Dict[str, Path] = {}

    def _text_image(
        text: str,
        bg=(255, 255, 255),
        fg=(0, 0, 0),
    ) -> Image.Image:
        # Draw at 1× then scale up — avoids needing TrueType fonts while
        # producing large-enough characters for reliable Tesseract recognition.
        small = Image.new("RGB", (600, 200), bg)
        draw = ImageDraw.Draw(small)
        draw.text((30, 80), text, fill=fg)
        return small.resize(
            (small.width * _SCALE, small.height * _SCALE),
            Image.NEAREST,
        )

    def _save(img: "Image.Image", path: Path) -> None:
        img.save(str(path), dpi=(_FIXTURE_DPI, _FIXTURE_DPI))

    base_text = GROUND_TRUTH["clean_scan"]

    # clean_scan
    p = image_dir / "clean_scan.png"
    _save(_text_image(base_text), p)
    paths["clean_scan"] = p

    # faded_scan — light grey background, medium grey ink
    p = image_dir / "faded_scan.png"
    _save(_text_image(base_text, bg=(240, 240, 240), fg=(130, 130, 130)), p)
    paths["faded_scan"] = p

    # rotated variants
    from PIL import Image as _PIL_Image
    base_img = _text_image(base_text)
    for deg, transpose_op, key in (
        (90,  _PIL_Image.ROTATE_90,  "rotated_90"),
        (180, _PIL_Image.ROTATE_180, "rotated_180"),
        (270, _PIL_Image.ROTATE_270, "rotated_270"),
    ):
        p = image_dir / f"{key}.png"
        _save(base_img.transpose(transpose_op), p)
        paths[key] = p

    # two_column — two text blocks side by side
    p = image_dir / "two_column.png"
    small = Image.new("RGB", (600, 200), (255, 255, 255))
    draw = ImageDraw.Draw(small)
    draw.text((30, 80),  "Left column text.",  fill=(0, 0, 0))
    draw.text((330, 80), "Right column text.", fill=(0, 0, 0))
    _save(small.resize((small.width * _SCALE, small.height * _SCALE), Image.NEAREST), p)
    paths["two_column"] = p

    # table — simple grid-like layout
    p = image_dir / "table.png"
    small = Image.new("RGB", (600, 200), (255, 255, 255))
    draw = ImageDraw.Draw(small)
    draw.text((30, 40), "Name  Age  City",   fill=(0, 0, 0))
    draw.text((30, 80), "Alice  30   Manila", fill=(0, 0, 0))
    draw.text((30, 110), "Bob    25   Davao",  fill=(0, 0, 0))
    _save(small.resize((small.width * _SCALE, small.height * _SCALE), Image.NEAREST), p)
    paths["table"] = p

    # blank — solid white
    p = image_dir / "blank.png"
    _save(Image.new("RGB", (2400, 800), (255, 255, 255)), p)
    paths["blank"] = p

    # mixed digital/scanned PDF — page 0 has digital text, page 1 is blank
    try:
        import fitz
        pdf_dir = fixture_dir / "pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        p = pdf_dir / "mixed_digital_scanned.pdf"
        doc = fitz.open()
        page0 = doc.new_page(width=612, height=792)
        page0.insert_text((72, 100), base_text * 3, fontsize=12)
        doc.new_page(width=612, height=792)  # blank page
        doc.save(str(p))
        doc.close()
        paths["mixed_digital_scanned"] = p
    except Exception as exc:
        print(f"[ocr_benchmark] Could not create mixed PDF fixture: {exc}")

    # Ground-truth text files
    for name, text in GROUND_TRUTH.items():
        gt_path = gt_dir / f"{name}.txt"
        gt_path.write_text(text, encoding="utf-8")

    return paths


# ---------------------------------------------------------------
# Per-fixture evaluation
# ---------------------------------------------------------------

def _evaluate_image_fixture(
    image_path: Path,
    ground_truth: str,
    lang: str,
    psm_values: List[int],
) -> Dict[str, Any]:
    """Run OCR + CER/WER on a single image for each PSM mode.

    Returns a per-fixture result dict.  Never raises — all errors
    are surfaced in the result["errors"] list.
    """
    from translator.services.ocr_stage.ocr_service import OCRService
    from translator.services.ocr_stage.qa_report import (
        calculate_cer,
        calculate_wer,
        build_document_qa_report,
    )

    result: Dict[str, Any] = {
        "filename": image_path.name,
        "ground_truth": ground_truth,
        "psm_results": [],
        "errors": [],
    }

    for psm in psm_values:
        svc = OCRService(psm=psm, lang=lang, detect_orientation=True)
        t0 = time.monotonic()
        try:
            pages = svc.extract_image_text_and_layout(str(image_path))
        except Exception as exc:
            result["psm_results"].append({
                "psm": psm,
                "hypothesis": "",
                "cer": None,
                "wer": None,
                "confidence": None,
                "error": str(exc),
                "processing_time_s": round(time.monotonic() - t0, 3),
            })
            result["errors"].append(f"PSM {psm}: {exc}")
            continue

        elapsed = round(time.monotonic() - t0, 3)
        hyp_lines = [
            line.get("text", "")
            for page in pages
            for block in page.get("blocks", [])
            if block.get("type") == "text"
            for line in block.get("lines", [])
        ]
        hypothesis = " ".join(hyp_lines).strip()

        # Confidence: mean block-level confidence
        confs = [
            float(b["confidence"])
            for page in pages
            for b in page.get("blocks", [])
            if b.get("confidence") is not None
        ]
        confidence = round(sum(confs) / len(confs), 4) if confs else None

        cer = calculate_cer(ground_truth, hypothesis) if ground_truth else None
        wer = calculate_wer(ground_truth, hypothesis) if ground_truth else None

        warnings_list = [
            page.get("ocr_warning") or page.get("ocr_error") or ""
            for page in pages
            if page.get("ocr_warning") or page.get("ocr_error")
        ]

        result["psm_results"].append({
            "psm": psm,
            "hypothesis": hypothesis,
            "cer": round(cer, 4) if cer is not None else None,
            "wer": round(wer, 4) if wer is not None else None,
            "confidence": confidence,
            "warnings": warnings_list,
            "processing_time_s": elapsed,
        })

    return result


# ---------------------------------------------------------------
# Management command
# ---------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Run real OCR evaluation across fixture images, benchmark PSM modes, "
        "and produce a QA report (JSON + CSV). "
        "Requires Tesseract to be installed and in PATH (or TESSERACT_CMD set)."
    )

    def add_arguments(self, parser):
        here = Path(__file__).resolve().parent.parent.parent
        default_fixtures = here / "ocr_fixtures"
        parser.add_argument(
            "--fixture-dir",
            default=str(default_fixtures),
            help="Directory containing fixture images and ground_truth/ sub-directory.",
        )
        parser.add_argument(
            "--output-dir",
            default=str(default_fixtures / "reports"),
            help="Directory where JSON and CSV reports are written.",
        )
        parser.add_argument(
            "--psm",
            nargs="+",
            type=int,
            default=[3, 4, 6],
            metavar="N",
            help="Tesseract PSM modes to benchmark (default: 3 4 6).",
        )
        parser.add_argument(
            "--lang",
            default="eng",
            help="Tesseract language code (default: eng).",
        )
        parser.add_argument(
            "--check-only",
            action="store_true",
            help="Only print the Tesseract environment report and exit.",
        )
        parser.add_argument(
            "--build-fixtures",
            action="store_true",
            help="(Re)generate synthetic fixture images before running evaluation.",
        )

    def handle(self, *args, **options):
        from translator.services.ocr_stage.environment import (
            check_tesseract_environment,
            format_environment_report,
        )

        env = check_tesseract_environment()
        self.stdout.write(format_environment_report(env))

        if options["check_only"]:
            if not env["available"]:
                raise CommandError(
                    "Tesseract is not available. "
                    "Install it and re-run without --check-only."
                )
            return

        if not env["available"]:
            raise CommandError(
                "Tesseract is not installed or not in PATH. "
                "Install Tesseract and set TESSERACT_CMD if needed, "
                "then re-run this command. "
                "See: https://github.com/tesseract-ocr/tesseract"
            )

        fixture_dir = Path(options["fixture_dir"])
        output_dir = Path(options["output_dir"])
        psm_values: List[int] = options["psm"]
        lang: str = options["lang"]

        # Build or rebuild fixtures when requested.
        if options["build_fixtures"] or not (fixture_dir / "images").is_dir():
            self.stdout.write("Building fixture images …")
            build_fixtures(fixture_dir)
            self.stdout.write(self.style.SUCCESS("Fixtures built."))

        gt_dir = fixture_dir / "ground_truth"
        image_dir = fixture_dir / "images"
        if not image_dir.is_dir():
            raise CommandError(
                f"Fixture images directory not found: {image_dir}. "
                "Run with --build-fixtures to generate them."
            )

        # Collect image fixtures.
        image_paths = sorted(image_dir.glob("*.png")) + sorted(image_dir.glob("*.jpg"))
        if not image_paths:
            raise CommandError(f"No image fixtures found in {image_dir}.")

        all_results: List[Dict[str, Any]] = []
        for image_path in image_paths:
            stem = image_path.stem
            gt_file = gt_dir / f"{stem}.txt"
            ground_truth = (
                gt_file.read_text(encoding="utf-8").strip()
                if gt_file.exists()
                else ""
            )
            self.stdout.write(f"  Evaluating {image_path.name} …")
            result = _evaluate_image_fixture(image_path, ground_truth, lang, psm_values)
            all_results.append(result)
            # Print per-PSM summary.
            for psm_r in result["psm_results"]:
                if psm_r.get("error"):
                    self.stdout.write(
                        f"    PSM {psm_r['psm']}: ERROR — {psm_r['error']}"
                    )
                else:
                    self.stdout.write(
                        f"    PSM {psm_r['psm']}: "
                        f"CER={psm_r['cer']} WER={psm_r['wer']} "
                        f"conf={psm_r['confidence']} "
                        f"time={psm_r['processing_time_s']}s"
                    )

        # Mixed PDF evaluation.
        pdf_dir = fixture_dir / "pdfs"
        mixed_pdf = pdf_dir / "mixed_digital_scanned.pdf"
        pdf_result: Optional[Dict[str, Any]] = None
        if mixed_pdf.exists():
            self.stdout.write("  Evaluating mixed_digital_scanned.pdf …")
            pdf_result = self._evaluate_mixed_pdf(mixed_pdf, lang)
            self.stdout.write(
                f"    extraction_method={pdf_result.get('extraction_method')} "
                f"page_methods={pdf_result.get('page_extraction_methods')}"
            )

        # Write reports.
        output_dir.mkdir(parents=True, exist_ok=True)
        report_data = {
            "tesseract_version": env.get("version"),
            "languages": env.get("languages"),
            "psm_modes_tested": psm_values,
            "lang": lang,
            "fixtures": all_results,
            "mixed_pdf": pdf_result,
        }
        json_path = output_dir / "ocr_benchmark_report.json"
        csv_path = output_dir / "ocr_benchmark_report.csv"

        json_path.write_text(
            json.dumps(report_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.stdout.write(self.style.SUCCESS(f"JSON report: {json_path}"))

        # Write CSV (one row per fixture × PSM combination).
        import csv as _csv
        csv_rows = []
        for res in all_results:
            for psm_r in res["psm_results"]:
                csv_rows.append({
                    "filename": res["filename"],
                    "page": 1,
                    "extraction_method": "ocr",
                    "confidence": psm_r.get("confidence"),
                    "cer": psm_r.get("cer"),
                    "wer": psm_r.get("wer"),
                    "processing_time_s": psm_r.get("processing_time_s"),
                    "psm": psm_r.get("psm"),
                    "warnings": "; ".join(psm_r.get("warnings", [])),
                    "pass_fail": (
                        "skip"
                        if psm_r.get("cer") is None
                        else ("pass" if psm_r["cer"] < 0.10 else "fail")
                    ),
                })

        fieldnames = [
            "filename", "page", "extraction_method", "confidence",
            "cer", "wer", "processing_time_s", "psm", "warnings", "pass_fail",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        self.stdout.write(self.style.SUCCESS(f"CSV report:  {csv_path}"))

        # PSM comparison summary.
        self.stdout.write("\n=== PSM Benchmark Summary ===")
        for psm in psm_values:
            cers = [
                r["cer"]
                for res in all_results
                for r in res["psm_results"]
                if r.get("psm") == psm and r.get("cer") is not None
            ]
            mean_cer = round(sum(cers) / len(cers), 4) if cers else None
            self.stdout.write(f"  PSM {psm}: mean CER = {mean_cer}")

        self.stdout.write(
            "\nPSM default (3) should only be changed when "
            "another mode shows consistent CER improvement on "
            "a representative evaluation set."
        )

    def _evaluate_mixed_pdf(self, pdf_path: Path, lang: str) -> Dict[str, Any]:
        """Run hybrid extraction on the mixed-PDF fixture and return routing info."""
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import DetectionType

        job = JobStatus("benchmark-mixed-pdf")
        job.detection_type = DetectionType.DIGITAL
        job.metadata = {}

        pipeline = PipelineService()
        layout_data, ocr_unavailable_msg = pipeline._extract_hybrid_pdf(
            str(pdf_path), [lang], job
        )

        return {
            "filename": pdf_path.name,
            "page_count": len(layout_data),
            "extraction_method": job.metadata.get("extraction_method"),
            "page_extraction_methods": job.metadata.get("page_extraction_methods"),
            "ocr_unavailable": ocr_unavailable_msg,
            "page_order": [p.get("page") for p in layout_data],
        }
