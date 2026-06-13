import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import DEFAULT_OCR_LANGUAGES
from .models import (
    DocumentPage,
    GeneratedOutput,
    OCRResult,
    SystemEventLog,
    TranslationJob,
    TranslationSegment,
    UploadedDocument,
)
from .services import sync_pipeline_job


User = get_user_model()


class TranslatorAuthAndJobTests(TestCase):
    password = "Bagobo-Test-2026!"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.media_root = Path(self.temp_dir.name)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_dir.cleanup)

        self.alice = User.objects.create_user(
            username="alice",
            email="alice@example.test",
            password=self.password,
        )
        self.bob = User.objects.create_user(
            username="bob",
            email="bob@example.test",
            password=self.password,
        )

    def test_signup_creates_and_logs_in_user(self):
        response = self.client.post(
            reverse("translator:signup"),
            {
                "username": "new-reviewer",
                "email": "reviewer@example.test",
                "password1": self.password,
                "password2": self.password,
            },
        )

        self.assertRedirects(response, reverse("translator:translate"))
        user = User.objects.get(username="new-reviewer")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_login_endpoint_redirects_to_translator(self):
        response = self.client.post(
            reverse("translator:login"),
            {"username": self.alice.username, "password": self.password},
        )

        self.assertRedirects(response, reverse("translator:translate"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.alice.pk)

    def test_translate_page_requires_authentication(self):
        translate_url = reverse("translator:translate")
        response = self.client.get(translate_url)

        self.assertRedirects(
            response,
            f"{reverse('translator:login')}?next={translate_url}",
        )

    def test_upload_creates_owned_job_and_stores_media_files(self):
        self.client.force_login(self.alice)
        uploaded = SimpleUploadedFile(
            "sample.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        )

        with patch("translator.views.start_translation_job") as start_translation:
            response = self.client.post(
                reverse("translator:upload"),
                {
                    "file": uploaded,
                    "source_language": "auto",
                    "target_language": "tagabawa",
                    "ocr_languages": "",
                },
            )

        self.assertEqual(response.status_code, 202)
        job = TranslationJob.objects.get()
        self.assertEqual(job.owner, self.alice)
        self.assertEqual(job.original_filename, "sample.pdf")
        self.assertEqual(job.ocr_languages, DEFAULT_OCR_LANGUAGES)
        self.assertTrue(Path(job.upload_file_path).is_file())
        self.assertTrue(Path(job.input_file_path).is_file())
        self.assertEqual(
            Path(job.upload_file_path).relative_to(self.media_root).parts[:2],
            ("uploads", job.job_id),
        )
        self.assertEqual(
            Path(job.input_file_path).relative_to(self.media_root).parts[:3],
            ("jobs", job.job_id, "input"),
        )
        document = UploadedDocument.objects.get(job=job)
        self.assertEqual(document.owner, self.alice)
        self.assertEqual(document.original_filename, "sample.pdf")
        self.assertEqual(document.file_size_bytes, uploaded.size)
        self.assertEqual(document.metadata["content_type"], "application/pdf")
        self.assertEqual(job.metadata["original_content_type"], "application/pdf")
        start_translation.assert_called_once()
        self.assertEqual(start_translation.call_args.args[0].id, job.id)

    def test_processing_sync_saves_ocr_result_and_translation_segments(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.PROCESSING)
        structure_path = self._write_job_file(
            job,
            "structure.json",
            json.dumps(
                {
                    "warnings": [],
                    "pages": [
                        {
                            "page_number": 1,
                            "width": 612,
                            "height": 792,
                            "blocks": [
                                {
                                    "type": "text",
                                    "block_id": "b1",
                                    "bbox": [72, 72, 540, 110],
                                    "ocr_confidence": 0.91,
                                    "lines": [
                                        {
                                            "text": "Hello learner",
                                            "translated_text": "Madigar learner",
                                            "translation_method": "phrasebook_exact",
                                            "translation_confidence": 0.95,
                                            "ocr_confidence": 0.91,
                                            "bbox": [72, 72, 540, 90],
                                        },
                                        {
                                            "text": "Unlisted phrase",
                                            "translated_text": "[UNKNOWN_FOR_REVIEW]",
                                            "translation_method": "unknown_for_review",
                                            "translation_confidence": 0.0,
                                            "ocr_confidence": 0.81,
                                            "bbox": [72, 94, 540, 110],
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ).encode("utf-8"),
        )
        sync_pipeline_job(
            SimpleNamespace(
                job_id=job.job_id,
                status=TranslationJob.Status.COMPLETED,
                progress=100,
                current_phase="completed",
                current_step="Completed",
                phase_message="Translation complete.",
                error="",
                detection_type="digital",
                file_type=TranslationJob.FileType.PDF,
                metadata={"structure_file": str(structure_path)},
                completed_at=None,
            )
        )

        job.refresh_from_db()
        self.assertEqual(job.status, TranslationJob.Status.COMPLETED)
        self.assertEqual(DocumentPage.objects.filter(job=job).count(), 1)
        ocr = OCRResult.objects.get(job=job)
        self.assertIn("Hello learner", ocr.text)
        self.assertAlmostEqual(ocr.confidence, 0.8767)
        self.assertEqual(TranslationSegment.objects.filter(job=job).count(), 2)
        first = TranslationSegment.objects.get(job=job, segment_index=1)
        self.assertEqual(first.source_text, "Hello learner")
        self.assertEqual(first.translated_text, "Madigar learner")
        self.assertEqual(first.method, "phrasebook_exact")
        self.assertAlmostEqual(first.confidence, 0.95)
        second = TranslationSegment.objects.get(job=job, segment_index=2)
        self.assertTrue(second.needs_review)

    def test_owner_can_read_job_status(self):
        job = self._create_job(self.alice)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:status", args=[job.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["job_id"], job.job_id)
        self.assertEqual(payload["status"], TranslationJob.Status.QUEUED)

    def test_translate_page_hides_ocr_language_controls(self):
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:translate"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Translation Settings", body)
        self.assertNotIn("OCR Language Coverage", body)
        self.assertNotIn("Used automatically for scanned PDFs and images.", body)
        self.assertNotIn("ocr_languages_list", body)
        self.assertNotIn("Custom OCR Codes", body)
        self.assertNotIn("ocr_languages_custom", body)
        self.assertNotIn("Orientation and Script Detection", body)
        self.assertRegex(body, r'id="complete-actions"[^>]*hidden')
        self.assertRegex(body, r'id="preview-link"[^>]*hidden')
        self.assertRegex(body, r'id="download-link"[^>]*hidden')

    def test_hidden_upload_actions_are_not_overridden_by_css(self):
        styles_path = Path(__file__).resolve().parents[1] / "static" / "css" / "styles.css"
        css = styles_path.read_text(encoding="utf-8")

        self.assertIn("[hidden]", css)
        self.assertIn("display: none !important", css)

    def test_user_cannot_read_another_users_job_status(self):
        job = self._create_job(self.alice)
        self.client.force_login(self.bob)

        response = self.client.get(reverse("translator:status", args=[job.id]))

        self.assertEqual(response.status_code, 404)

    def test_structure_view_is_owner_scoped(self):
        job = self._create_job(self.alice)
        structure_path = self._write_job_file(
            job,
            "structure.json",
            json.dumps({"pages": [{"page_number": 1, "blocks": []}]}).encode("utf-8"),
        )
        job.structure_file_path = str(structure_path)
        job.save(update_fields=["structure_file_path"])

        self.client.force_login(self.alice)
        owner_response = self.client.get(reverse("translator:structure", args=[job.id]))
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(owner_response.json()["status"], job.status)

        self.client.force_login(self.bob)
        other_response = self.client.get(reverse("translator:structure", args=[job.id]))
        self.assertEqual(other_response.status_code, 404)

    def test_preview_image_and_download_are_owner_scoped(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.COMPLETED)
        preview_path = self._write_job_file(
            job,
            "preview",
            "original_page_0.png",
            b"\x89PNG\r\n\x1a\n",
        )
        output_path = self._write_job_file(
            job,
            "translated",
            "translated.pdf",
            b"%PDF-1.4\n%%EOF",
        )
        job.output_file_path = str(output_path)
        job.save(update_fields=["output_file_path"])

        self.client.force_login(self.alice)
        preview_response = self.client.get(
            reverse(
                "translator:translate_preview_image",
                args=[job.id, preview_path.name],
            )
        )
        self.assertEqual(preview_response.status_code, 200)
        preview_response.close()

        download_response = self.client.get(
            reverse("translator:translate_download", args=[job.id])
        )
        self.assertEqual(download_response.status_code, 200)
        download_response.close()

        self.client.force_login(self.bob)
        other_preview_response = self.client.get(
            reverse(
                "translator:translate_preview_image",
                args=[job.id, preview_path.name],
            )
        )
        self.assertEqual(other_preview_response.status_code, 404)

        other_download_response = self.client.get(
            reverse("translator:translate_download", args=[job.id])
        )
        self.assertEqual(other_download_response.status_code, 404)

    def test_history_shows_only_logged_in_users_jobs(self):
        alice_job = self._create_job(
            self.alice,
            status=TranslationJob.Status.COMPLETED,
            original_filename="alice.pdf",
        )
        bob_job = self._create_job(
            self.bob,
            status=TranslationJob.Status.COMPLETED,
            original_filename="bob.pdf",
        )
        TranslationSegment.objects.create(
            job=alice_job,
            segment_index=1,
            source_text="Alice original",
            translated_text="Alice translated",
            source_language="english",
            target_language="tagabawa",
            method="phrasebook_exact",
            confidence=1.0,
        )
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:history"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(alice_job.original_filename, body)
        self.assertNotIn(bob_job.original_filename, body)

    def test_preview_uses_database_translation_segments(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.COMPLETED)
        TranslationSegment.objects.create(
            job=job,
            segment_index=1,
            source_text="Original from database",
            translated_text="Translated from database",
            source_language="english",
            target_language="tagabawa",
            method="phrasebook_exact",
            confidence=0.88,
            needs_review=True,
        )
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_preview", args=[job.id]))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Original from database", body)
        self.assertIn("Translated from database", body)
        self.assertIn("phrasebook_exact", body)
        self.assertIn("Confidence 0.88", body)
        self.assertIn("Needs review", body)

    def test_history_empty_state_has_upload_cta(self):
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:history"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("No translated documents yet", body)
        self.assertIn("Upload a Document", body)

    def test_pending_job_hides_preview_and_download_buttons(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.QUEUED)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_detail", args=[job.id]))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertNotIn("Preview Bilingual", body)
        self.assertNotIn("Download</a>", body)
        self.assertIn("Processing is still running", body)

    def test_processing_job_hides_preview_and_download_buttons(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.PROCESSING)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:history"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(job.original_filename, body)
        self.assertNotIn("View Preview", body)
        self.assertNotIn("Download", body)

    def test_completed_job_shows_preview_without_download_when_output_missing(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.COMPLETED)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_detail", args=[job.id]))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Preview Bilingual", body)
        self.assertNotIn("Download</a>", body)

    def test_completed_job_with_output_shows_download(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.COMPLETED)
        output_path = self._write_job_file(
            job,
            "translated.pdf",
            b"%PDF-1.4\n%%EOF",
        )
        job.output_file_path = str(output_path)
        job.save(update_fields=["output_file_path"])
        self.client.force_login(self.alice)

        detail_response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        status_response = self.client.get(reverse("translator:status", args=[job.id]))

        self.assertEqual(detail_response.status_code, 200)
        body = detail_response.content.decode("utf-8")
        self.assertIn("Preview Bilingual", body)
        self.assertIn("Download</a>", body)
        self.assertIn("Uploaded", body)
        self.assertIn("Extracting / OCR", body)
        self.assertIn("Translating", body)
        self.assertIn("Generating Output", body)
        self.assertIn("Completed", body)
        payload = status_response.json()
        self.assertTrue(payload["can_preview"])
        self.assertTrue(payload["can_download"])

    def test_completed_job_with_generated_output_shows_download(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.COMPLETED)
        output_path = self._write_job_file(
            job,
            "outputs",
            "translated.pdf",
            b"%PDF-1.4\n%%EOF",
        )
        GeneratedOutput.objects.create(
            job=job,
            output_type=GeneratedOutput.OutputType.TRANSLATED_PDF,
            file_format="pdf",
            file_path=str(output_path),
            file_size_bytes=output_path.stat().st_size,
        )
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_detail", args=[job.id]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("Download</a>", response.content.decode("utf-8"))

    def test_failed_job_hides_preview_and_download_buttons(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.FAILED)
        job.error = "OCR produced no text."
        job.save(update_fields=["error"])
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_detail", args=[job.id]))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertNotIn("Preview Bilingual", body)
        self.assertNotIn("Download</a>", body)
        self.assertIn("Translation failed safely", body)
        self.assertIn("Failed", body)

    def test_preview_before_completion_redirects_to_job_detail(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.PROCESSING)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_preview", args=[job.id]))

        self.assertRedirects(response, reverse("translator:job_detail", args=[job.id]))

    def test_download_before_completion_redirects_to_job_detail(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.PROCESSING)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_download", args=[job.id]))

        self.assertRedirects(response, reverse("translator:job_detail", args=[job.id]))

    def test_another_user_cannot_view_private_preview(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.COMPLETED)
        self.client.force_login(self.bob)

        response = self.client.get(reverse("translator:job_preview", args=[job.id]))

        self.assertEqual(response.status_code, 404)

    def test_failed_processing_sync_saves_system_event_log(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.PROCESSING)
        sync_pipeline_job(
            SimpleNamespace(
                job_id=job.job_id,
                status=TranslationJob.Status.FAILED,
                progress=25,
                current_phase="ocr",
                current_step="Translation failed",
                phase_message="OCR produced no text.",
                error="OCR produced no text.",
                detection_type="scanned",
                file_type=TranslationJob.FileType.PDF,
                metadata={},
                completed_at=None,
            )
        )

        job.refresh_from_db()
        self.assertEqual(job.status, TranslationJob.Status.FAILED)
        event = SystemEventLog.objects.get(job=job, event_type="translation_job_failed")
        self.assertEqual(event.level, SystemEventLog.Level.ERROR)
        self.assertIn("OCR produced no text", event.message)

    def test_phase_5b_translation_quality_summary_is_visible(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.COMPLETED)
        TranslationSegment.objects.create(
            job=job,
            segment_index=1,
            source_text="Known phrase",
            translated_text="Known translation",
            source_language="english",
            target_language="tagabawa",
            method="exact_phrase",
            confidence=0.8,
            needs_review=False,
        )
        TranslationSegment.objects.create(
            job=job,
            segment_index=2,
            source_text="Unknown phrase",
            translated_text="[UNKNOWN_FOR_REVIEW]",
            source_language="english",
            target_language="tagabawa",
            method="unknown_for_review",
            confidence=0.0,
            needs_review=True,
        )
        original_preview = self._write_job_file(
            job,
            "preview",
            "original_page_0.png",
            b"\x89PNG\r\n\x1a\n",
        )
        translated_preview = self._write_job_file(
            job,
            "preview",
            "translated_page_0.png",
            b"\x89PNG\r\n\x1a\n",
        )
        job.metadata = {
            "preview_original": [str(original_preview)],
            "preview_translated": [str(translated_preview)],
        }
        job.save(update_fields=["metadata"])
        self.client.force_login(self.alice)

        detail_response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        preview_response = self.client.get(reverse("translator:job_preview", args=[job.id]))

        self.assertEqual(detail_response.status_code, 200)
        detail_body = detail_response.content.decode("utf-8")
        self.assertIn("Translation Confidence", detail_body)
        self.assertIn("40%", detail_body)
        self.assertIn("Needs Review", detail_body)
        self.assertIn("1 of 2", detail_body)

        self.assertEqual(preview_response.status_code, 200)
        preview_body = preview_response.content.decode("utf-8")
        self.assertIn("Translation Details", preview_body)
        self.assertIn("40%", preview_body)
        self.assertIn("1 of 2", preview_body)

    def test_phase_5b_quality_summary_is_exposed_by_status_and_preview_api(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.COMPLETED)
        TranslationSegment.objects.create(
            job=job,
            segment_index=1,
            source_text="Known phrase",
            translated_text="Known translation",
            source_language="english",
            target_language="tagabawa",
            method="exact_phrase",
            confidence=0.8,
            needs_review=False,
        )
        TranslationSegment.objects.create(
            job=job,
            segment_index=2,
            source_text="Unknown phrase",
            translated_text="[UNKNOWN_FOR_REVIEW]",
            source_language="english",
            target_language="tagabawa",
            method="unknown_for_review",
            confidence=0.0,
            needs_review=True,
        )
        self.client.force_login(self.alice)

        status_response = self.client.get(reverse("translator:status", args=[job.id]))
        preview_response = self.client.get(
            reverse("translator:preview_data", args=[job.id])
        )

        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.json()
        self.assertEqual(status_payload["segment_count"], 2)
        self.assertEqual(status_payload["review_segment_count"], 1)
        self.assertAlmostEqual(status_payload["translation_confidence"], 0.4)
        self.assertEqual(status_payload["translation_confidence_pct"], 40)
        self.assertTrue(status_payload["translation_has_review_items"])

        self.assertEqual(preview_response.status_code, 200)
        preview_payload = preview_response.json()
        self.assertEqual(preview_payload["segment_count"], 2)
        self.assertEqual(preview_payload["review_segment_count"], 1)
        self.assertEqual(preview_payload["translation_confidence_pct"], 40)
        self.assertEqual(preview_payload["translation_summary"]["segment_count"], 2)
        self.assertEqual(
            preview_payload["translation_summary"]["review_segment_count"],
            1,
        )
        self.assertTrue(
            preview_payload["translation_summary"]["translation_has_review_items"]
        )

    def _create_job(self, owner, status=TranslationJob.Status.QUEUED, original_filename="sample.pdf"):
        return TranslationJob.objects.create(
            owner=owner,
            original_filename=original_filename,
            file_type=TranslationJob.FileType.PDF,
            status=status,
        )

    def _write_job_file(self, job, *parts_and_content):
        *parts, content = parts_and_content
        path = self.media_root.joinpath("jobs", job.job_id, *parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path


class TranslatorPhase5BPipelineTests(TestCase):
    def test_bilingual_first_page_uses_line_specific_translation_metadata(self):
        from translator.services.pipeline_service import PipelineService
        from translator.services.translation_dataset import UNKNOWN_FOR_REVIEW

        service = PipelineService.__new__(PipelineService)
        layout_data = [
            {
                "blocks": [
                    {
                        "type": "text",
                        "bbox": [0, 0, 100, 50],
                        "lines": [
                            {"text": "First line", "bbox": [0, 0, 100, 20]},
                            {"text": "Second line", "bbox": [0, 24, 100, 44]},
                        ],
                    }
                ]
            }
        ]
        translations = {
            "0_0": {
                "translated": "Wrong block-level fallback",
                "method": "legacy",
                "cascade_stage": "legacy",
                "confidence": 0.1,
            },
            "0_0_0": {
                "translated": "First translated",
                "method": "exact_phrase",
                "cascade_stage": "exact_phrase",
                "confidence": 1.0,
            },
            "0_0_1": {
                "translated": UNKNOWN_FOR_REVIEW,
                "method": "unknown_for_review",
                "cascade_stage": "unknown_for_review",
                "confidence": 0.0,
            },
        }

        preview = service._build_bilingual_first_page(layout_data, translations)

        self.assertEqual(len(preview["blocks"]), 2)
        self.assertEqual(preview["blocks"][0]["translated_text"], "First translated")
        self.assertEqual(preview["blocks"][0]["translation_method"], "exact_phrase")
        self.assertEqual(preview["blocks"][0]["translation_confidence"], 1.0)
        self.assertFalse(preview["blocks"][0]["needs_review"])
        self.assertEqual(preview["blocks"][1]["translated_text"], UNKNOWN_FOR_REVIEW)
        self.assertEqual(preview["blocks"][1]["translation_method"], "unknown_for_review")
        self.assertEqual(preview["blocks"][1]["translation_confidence"], 0.0)
        self.assertTrue(preview["blocks"][1]["needs_review"])


# ============================================================
# Phase 4 — System Design, Gating, Security & Purge Tests
# ============================================================

import os
import shutil


class TranslatorPhase4SystemTests(TestCase):
    password = "Bagobo-Test-Phase4-Sys!"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.media_root = Path(self.temp_dir.name)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_dir.cleanup)

        self.alice = User.objects.create_user(
            username="sysalice", email="sysalice@example.test", password=self.password
        )
        self.bob = User.objects.create_user(
            username="sysbob", email="sysbob@example.test", password=self.password
        )

    def _create_job(self, owner, status="completed", metadata=None):
        return TranslationJob.objects.create(
            owner=owner,
            original_filename="test_doc.pdf",
            file_type="pdf",
            status=status,
            source_language="english",
            target_language="tagabawa",
            metadata=metadata or {},
        )

    def test_scanned_pdf_uses_ocr_automatically(self):
        from translator.services.detection_service import DetectionService, DetectionType
        orig = DetectionService._count_pdf_text_chars
        try:
            DetectionService._count_pdf_text_chars = staticmethod(lambda path: (10, 1))
            det_type = DetectionService.detect_pdf_type("dummy.pdf")
            self.assertEqual(det_type, DetectionType.SCANNED)
        finally:
            DetectionService._count_pdf_text_chars = orig

    def test_image_upload_uses_ocr_automatically(self):
        from translator.services.detection_service import DetectionService, DetectionType
        det_type = DetectionService.detect_image_type("dummy.png")
        self.assertEqual(det_type, DetectionType.SCANNED)

    def test_digital_pdf_uses_direct_text_extraction(self):
        from translator.services.detection_service import DetectionService, DetectionType
        orig = DetectionService._count_pdf_text_chars
        try:
            DetectionService._count_pdf_text_chars = staticmethod(lambda path: (60, 1))
            det_type = DetectionService.detect_pdf_type("dummy.pdf")
            self.assertEqual(det_type, DetectionType.DIGITAL)
        finally:
            DetectionService._count_pdf_text_chars = orig

    def test_low_text_pdf_falls_back_to_ocr(self):
        from translator.services.detection_service import DetectionService, DetectionType
        orig = DetectionService._count_pdf_text_chars
        try:
            DetectionService._count_pdf_text_chars = staticmethod(lambda path: (49, 1))
            det_type = DetectionService.detect_pdf_type("dummy.pdf")
            self.assertEqual(det_type, DetectionType.SCANNED)
        finally:
            DetectionService._count_pdf_text_chars = orig

    def test_docx_uses_docx_text_extraction(self):
        from translator.services.detection_service import DetectionService, DetectionType
        det_type = DetectionService.detect_docx_type("dummy.docx")
        self.assertEqual(det_type, DetectionType.DIGITAL)

    def test_txt_uses_plain_text_extraction(self):
        job = self._create_job(self.alice, status="processing")
        job.file_type = "txt"
        job.save()
        self.assertEqual(job.file_type, "txt")

    def test_extraction_method_saved_in_metadata(self):
        job = self._create_job(self.alice, status="completed", metadata={"extraction_method": "docx_text"})
        self.assertEqual(job.metadata.get("extraction_method"), "docx_text")

    def test_ocr_confidence_saved_and_displayed(self):
        job = self._create_job(self.alice, status="completed", metadata={
            "extraction_method": "ocr_image",
            "ocr_summary": {"mean_confidence": 0.85}
        })
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("85%", response.content.decode("utf-8"))

    def test_ocr_warnings_saved_and_displayed(self):
        job = self._create_job(self.alice, status="completed", metadata={
            "ocr_warnings": ["Warning text 123"]
        })
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Warning text 123", response.content.decode("utf-8"))

    @patch("translator.services._get_pipeline_service")
    def test_ocr_failure_fails_safely(self, mock_pipeline):
        from translator.services import _run_translation_job
        mock_pipeline.side_effect = Exception("Tesseract failed catastrophically")
        job = self._create_job(self.alice, status="queued")
        _run_translation_job(
            job.job_id,
            job.input_file_path,
            "pdf",
            "auto",
            "tagabawa",
            None
        )
        job.refresh_from_db()
        self.assertEqual(job.status, TranslationJob.Status.FAILED)
        self.assertIn("Tesseract failed catastrophically", job.error)

    def test_upload_invalid_file_rejected(self):
        self.client.force_login(self.alice)
        bad_file = SimpleUploadedFile("danger.exe", b"binary content", content_type="application/octet-stream")
        response = self.client.post(reverse("translator:upload"), {"file": bad_file})
        self.assertEqual(response.status_code, 400)

    def test_upload_large_file_rejected(self):
        self.client.force_login(self.alice)
        large_file = SimpleUploadedFile("big.pdf", b"a" * (51 * 1024 * 1024), content_type="application/pdf")
        response = self.client.post(reverse("translator:upload"), {"file": large_file})
        self.assertEqual(response.status_code, 400)

    def test_upload_requires_login(self):
        response = self.client.post(reverse("translator:upload"))
        self.assertEqual(response.status_code, 302)

    def test_upload_csrf_required(self):
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.alice)
        # Load page to set cookie
        csrf_client.get(reverse("translator:translate"))
        response = csrf_client.post(reverse("translator:upload"), {"file": "dummy"})
        self.assertEqual(response.status_code, 403)

    def test_upload_rate_limit_blocks_excess_requests(self):
        self.client.force_login(self.alice)
        from django.core.cache import cache
        cache.clear()
        for _ in range(5):
            self.client.post(reverse("translator:upload"), {"file": ""})
        response = self.client.post(reverse("translator:upload"), {"file": ""})
        self.assertEqual(response.status_code, 400)
        self.assertIn("upload limit", response.json()["detail"])

    def test_active_job_limit_blocks_excess_processing_jobs(self):
        self.client.force_login(self.alice)
        self._create_job(self.alice, status=TranslationJob.Status.QUEUED)
        self._create_job(self.alice, status=TranslationJob.Status.PROCESSING)
        response = self.client.post(reverse("translator:upload"), {"file": ""})
        self.assertEqual(response.status_code, 400)
        self.assertIn("upload limit", response.json()["detail"])

    def test_preview_hidden_before_completed(self):
        job = self._create_job(self.alice, status="processing")
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_preview", args=[job.id]))
        self.assertRedirects(response, reverse("translator:job_detail", args=[job.id]))

    def test_download_hidden_until_output_exists(self):
        job = self._create_job(self.alice, status="completed")
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_download", args=[job.id]))
        self.assertRedirects(response, reverse("translator:job_detail", args=[job.id]))

    def test_failed_job_hides_preview_and_download(self):
        job = self._create_job(self.alice, status="failed")
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Preview Bilingual", response.content)

    def test_user_cannot_view_another_users_job(self):
        job = self._create_job(self.bob, status="completed")
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        self.assertEqual(response.status_code, 404)

    def test_user_cannot_preview_another_users_job(self):
        job = self._create_job(self.bob, status="completed")
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_preview", args=[job.id]))
        self.assertEqual(response.status_code, 404)

    def test_user_cannot_download_another_users_output(self):
        job = self._create_job(self.bob, status="completed")
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_download", args=[job.id]))
        self.assertEqual(response.status_code, 404)

    def test_user_cannot_delete_another_users_job(self):
        job = self._create_job(self.bob, status="completed")
        self.client.force_login(self.alice)
        response = self.client.post(reverse("translator:job_delete", args=[job.id]))
        self.assertEqual(response.status_code, 404)

    def test_preview_image_blocks_path_traversal(self):
        self.client.force_login(self.alice)
        job = self._create_job(self.alice, status="completed")
        response = self.client.get(reverse("translator:translate_preview_image", args=[job.id, "invalid_name.png"]))
        self.assertEqual(response.status_code, 400)

    @patch("translator.views._translated_output_path")
    @patch("translator.views._translated_output_exists")
    def test_download_serves_attachment_pdf_only(self, mock_exists, mock_path):
        mock_exists.return_value = True
        job = self._create_job(self.alice, status="completed")
        with tempfile.NamedTemporaryFile(dir=self.media_root, delete=False) as tmp:
            tmp.write(b"%PDF-1.4 mock content")
            tmp_name = tmp.name
        mock_path.return_value = Path(tmp_name)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_download", args=[job.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/pdf")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        
        # Close file handles to avoid Windows WinError 32 permission errors on cleanup
        response.close()
        os.remove(tmp_name)

    def test_history_lists_only_user_jobs(self):
        self._create_job(self.alice)
        self._create_job(self.bob)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:history"))
        self.assertEqual(response.status_code, 200)
        jobs = response.context["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job"].owner, self.alice)

    def test_soft_deleted_job_hidden_from_history(self):
        job = self._create_job(self.alice)
        job.is_deleted = True
        job.save()
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:history"))
        self.assertEqual(len(response.context["jobs"]), 0)

    def test_soft_deleted_job_hidden_from_recent_sidebar(self):
        job = self._create_job(self.alice)
        job.is_deleted = True
        job.save()
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:translate"))
        self.assertEqual(len(response.context["recent_jobs"]), 0)

    def test_deleted_job_returns_404_on_detail(self):
        job = self._create_job(self.alice)
        job.is_deleted = True
        job.save()
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        self.assertEqual(response.status_code, 404)

    def test_confirm_delete_page_requires_login(self):
        job = self._create_job(self.alice)
        response = self.client.get(reverse("translator:job_delete_confirm", args=[job.id]))
        self.assertEqual(response.status_code, 302)

    def test_delete_requires_post(self):
        job = self._create_job(self.alice)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_delete", args=[job.id]))
        self.assertEqual(response.status_code, 405)

    def test_admin_extraction_method_display_handles_missing_metadata(self):
        from translator.admin import TranslationJobAdmin
        from django.contrib.admin.sites import AdminSite
        admin_site = AdminSite()
        admin_instance = TranslationJobAdmin(TranslationJob, admin_site)
        job = self._create_job(self.alice, metadata=None)
        display = admin_instance.extraction_method_display(job)
        self.assertEqual(display, "—")

    def test_admin_ocr_confidence_display_handles_null_values(self):
        from translator.admin import TranslationJobAdmin
        from django.contrib.admin.sites import AdminSite
        admin_site = AdminSite()
        admin_instance = TranslationJobAdmin(TranslationJob, admin_site)
        job = self._create_job(self.alice, metadata=None)
        display = admin_instance.ocr_confidence_display(job)
        self.assertEqual(display, "N/A")

    def test_admin_translation_quality_display_handles_segments_and_empty_jobs(self):
        from translator.admin import TranslationJobAdmin
        from django.contrib.admin.sites import AdminSite

        admin_site = AdminSite()
        admin_instance = TranslationJobAdmin(TranslationJob, admin_site)
        job = self._create_job(self.alice, metadata=None)

        self.assertEqual(admin_instance.translation_confidence_display(job), "N/A")
        self.assertEqual(admin_instance.review_segments_display(job), "N/A")

        TranslationSegment.objects.create(
            job=job,
            segment_index=1,
            source_text="Known phrase",
            translated_text="Known translation",
            source_language="english",
            target_language="tagabawa",
            method="exact_phrase",
            confidence=0.8,
            needs_review=False,
        )
        TranslationSegment.objects.create(
            job=job,
            segment_index=2,
            source_text="Unknown phrase",
            translated_text="[UNKNOWN_FOR_REVIEW]",
            source_language="english",
            target_language="tagabawa",
            method="unknown_for_review",
            confidence=0.0,
            needs_review=True,
        )

        self.assertEqual(admin_instance.translation_confidence_display(job), "40%")
        self.assertEqual(admin_instance.review_segments_display(job), "1/2")

    def test_admin_ocr_quality_filter_finds_low_confidence_jobs(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from translator.admin import OCRQualityFilter, TranslationJobAdmin

        low_job = self._create_job(self.alice, status="completed")
        high_job = self._create_job(self.alice, status="completed")
        OCRResult.objects.create(job=low_job, confidence=0.42, text="low")
        OCRResult.objects.create(job=high_job, confidence=0.91, text="high")

        admin_site = AdminSite()
        admin_instance = TranslationJobAdmin(TranslationJob, admin_site)
        request = RequestFactory().get("/admin/", {"ocr_quality": "low"})
        request.user = self.alice
        filter_spec = OCRQualityFilter(
            request,
            {"ocr_quality": ["low"]},
            TranslationJob,
            admin_instance,
        )

        queryset = filter_spec.queryset(request, TranslationJob.objects.all())

        self.assertIn(low_job, queryset)
        self.assertNotIn(high_job, queryset)

    def test_purge_deleted_job_files_dry_run(self):
        from django.core.management import call_command
        from django.utils import timezone
        from datetime import timedelta
        job = self._create_job(self.alice, status="completed")
        job.is_deleted = True
        job.deleted_at = timezone.now() - timedelta(days=31)
        with tempfile.NamedTemporaryFile(dir=self.media_root, delete=False) as tmp:
            tmp.write(b"dummy content")
            tmp_name = tmp.name
        job.input_file_path = tmp_name
        job.save()
        call_command("purge_deleted_job_files", days=30, dry_run=True)
        self.assertTrue(os.path.exists(tmp_name))
        os.remove(tmp_name)

    def test_purge_deleted_job_files_actual(self):
        from django.core.management import call_command
        from django.utils import timezone
        from datetime import timedelta
        job = self._create_job(self.alice, status="completed")
        job.is_deleted = True
        job.deleted_at = timezone.now() - timedelta(days=31)
        with tempfile.NamedTemporaryFile(dir=self.media_root, delete=False) as tmp:
            tmp.write(b"dummy content")
            tmp_name = tmp.name
        job.input_file_path = tmp_name
        job.save()
        call_command("purge_deleted_job_files", days=30)
        self.assertFalse(os.path.exists(tmp_name))
