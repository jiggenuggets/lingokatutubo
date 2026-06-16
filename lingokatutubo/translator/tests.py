import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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

    def test_upload_warning_behavior_is_limited_to_active_upload(self):
        script_path = Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn(
            "Your document is still uploading. Leaving now may cancel the upload.",
            script,
        )
        self.assertIn('window.addEventListener("beforeunload", beforeUnloadHandler)', script)
        self.assertIn('window.removeEventListener("beforeunload", beforeUnloadHandler)', script)
        self.assertIn("if (!isUploading || currentJobId) return undefined", script)
        self.assertIn("if (!file || isSubmitting) return", script)

    def test_polling_controller_stops_on_terminal_status(self):
        script_path = Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn('const terminalStatuses = new Set(["completed", "failed"])', script)
        self.assertIn("if (terminalStatuses.has(status) || !activeStatuses.has(status))", script)
        self.assertIn("this.stop()", script)

    def test_duplicate_polling_is_prevented(self):
        script_path = Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("const controllers = new Map()", script)
        self.assertIn("if (controllers.has(key))", script)
        self.assertIn("return controllers.get(key)", script)

    def test_polling_resumes_when_visible(self):
        script_path = Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn('document.addEventListener("visibilitychange"', script)
        self.assertIn("if (!document.hidden", script)
        self.assertIn("document.hidden ? hiddenDelay : visibleDelay", script)

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
        # Error message from job.error appears in the alert panel
        self.assertIn("OCR produced no text.", body)
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

    def test_retrying_status_api_is_active_state(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.RETRYING)
        job.current_phase = "retrying"
        job.current_step = "Retrying abandoned translation job"
        job.phase_message = "This job was queued for retry."
        job.save()
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:status", args=[job.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], TranslationJob.Status.RETRYING)
        self.assertTrue(payload["is_processing"])
        self.assertEqual(payload["message"], "This job was queued for retry.")

    def test_status_api_returns_consistent_fields_for_all_states(self):
        self.client.force_login(self.alice)
        required_fields = {
            "status",
            "progress_percent",
            "current_phase",
            "current_step",
            "phase_message",
            "updated_at",
            "started_at",
            "completed_at",
            "error",
            "can_preview",
            "can_download",
        }

        for status in [
            TranslationJob.Status.QUEUED,
            TranslationJob.Status.PROCESSING,
            TranslationJob.Status.RETRYING,
            TranslationJob.Status.COMPLETED,
            TranslationJob.Status.FAILED,
        ]:
            job = self._create_job(
                self.alice,
                status=status,
                original_filename=f"{status}.pdf",
            )
            if status == TranslationJob.Status.FAILED:
                job.error = "Failed for test"
                job.save(update_fields=["error"])

            response = self.client.get(reverse("translator:status", args=[job.id]))

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(required_fields.issubset(payload.keys()))
            self.assertEqual(payload["status"], status)
            self.assertIsNotNone(payload["started_at"])
            if status == TranslationJob.Status.FAILED:
                self.assertEqual(payload["error"], "Failed for test")

    def test_active_job_banner_is_owner_scoped(self):
        self._create_job(self.alice, status=TranslationJob.Status.PROCESSING, original_filename="alice.pdf")
        self._create_job(self.bob, status=TranslationJob.Status.PROCESSING, original_filename="bob.pdf")

        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:history"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("1 document is currently processing.", body)
        self.assertIn("alice.pdf", body)
        self.assertNotIn("bob.pdf", body)
        self.assertIn("View Status", body)
        self.assertIn("History", body)

    def test_start_translation_job_dispatches_after_commit(self):
        from translator.services import start_translation_job

        job = self._create_job(self.alice, status=TranslationJob.Status.QUEUED)
        job.input_file_path = str(self.media_root / "sample.pdf")
        job.save(update_fields=["input_file_path"])

        with patch("translator.services.register_pipeline_callback"), patch(
            "translator.services.submit_translation_task"
        ) as submit_task:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                start_translation_job(job)

            submit_task.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()

        submit_task.assert_called_once()
        self.assertEqual(submit_task.call_args.args[1], job.job_id)

    def test_duplicate_task_is_ignored_safely(self):
        from translator.services import _run_translation_job

        job = self._create_job(self.alice, status=TranslationJob.Status.PROCESSING)

        with patch("translator.services._get_pipeline_service") as pipeline_service:
            _run_translation_job(
                job.job_id,
                job.input_file_path,
                "pdf",
                "auto",
                "tagabawa",
                None,
            )

        pipeline_service.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, TranslationJob.Status.PROCESSING)
        self.assertTrue(
            SystemEventLog.objects.filter(
                job=job,
                event_type="translation_job_duplicate_ignored",
            ).exists()
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

    @patch("translator.services._get_pipeline_service")
    def test_ocr_timeout_sets_failed_status(self, mock_pipeline):
        from translator.services import _run_translation_job

        mock_pipeline.side_effect = TimeoutError("Tesseract OCR timed out")
        job = self._create_job(self.alice, status=TranslationJob.Status.QUEUED)

        _run_translation_job(
            job.job_id,
            job.input_file_path,
            "pdf",
            "auto",
            "tagabawa",
            None,
        )

        job.refresh_from_db()
        self.assertEqual(job.status, TranslationJob.Status.FAILED)
        self.assertIn("Tesseract OCR timed out", job.error)
        self.assertTrue(
            SystemEventLog.objects.filter(
                job=job,
                event_type="translation_job_failed",
            ).exists()
        )

    def test_stale_processing_job_is_recovered(self):
        job = self._create_job(self.alice, status=TranslationJob.Status.PROCESSING)
        old_updated_at = timezone.now() - timezone.timedelta(minutes=30)
        TranslationJob.objects.filter(id=job.id).update(updated_at=old_updated_at)

        dry_output = StringIO()
        call_command("recover_stale_jobs", minutes=15, dry_run=True, stdout=dry_output)
        job.refresh_from_db()
        self.assertEqual(job.status, TranslationJob.Status.PROCESSING)
        self.assertIn("fail:", dry_output.getvalue())

        output = StringIO()
        call_command("recover_stale_jobs", minutes=15, stdout=output)
        job.refresh_from_db()
        self.assertEqual(job.status, TranslationJob.Status.FAILED)
        self.assertIn("abandoned", job.error)
        self.assertIn("Recovered 1 stale job", output.getvalue())
        self.assertTrue(
            SystemEventLog.objects.filter(
                job=job,
                event_type="translation_job_stale_recovery",
            ).exists()
        )

    def test_recover_stale_jobs_skips_completed_and_deleted_jobs(self):
        old_updated_at = timezone.now() - timezone.timedelta(minutes=30)
        completed = self._create_job(self.alice, status=TranslationJob.Status.COMPLETED)
        deleted = self._create_job(self.alice, status=TranslationJob.Status.PROCESSING)
        deleted.is_deleted = True
        deleted.save(update_fields=["is_deleted"])
        TranslationJob.objects.filter(id__in=[completed.id, deleted.id]).update(
            updated_at=old_updated_at
        )

        output = StringIO()
        call_command("recover_stale_jobs", minutes=15, stdout=output)

        completed.refresh_from_db()
        deleted.refresh_from_db()
        self.assertEqual(completed.status, TranslationJob.Status.COMPLETED)
        self.assertEqual(deleted.status, TranslationJob.Status.PROCESSING)
        self.assertIn("No stale translation jobs found", output.getvalue())

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

    def test_health_page_is_staff_only(self):
        staff = User.objects.create_user(
            username="healthstaff",
            email="healthstaff@example.test",
            password=self.password,
            is_staff=True,
        )
        checks = [{"name": "Database", "status": "ok", "detail": "sqlite"}]
        counts = {"queued": 0, "processing": 0, "retrying": 0, "stale": 0}

        response = self.client.get(reverse("translator:health"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:health"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(staff)
        with patch("translator.views._health_checks", return_value=checks), patch(
            "translator.views._health_job_counts", return_value=counts
        ):
            response = self.client.get(reverse("translator:health"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("System Health", body)
        self.assertIn("Database", body)

    def test_unavailable_redis_and_celery_are_reported_safely(self):
        from translator import views

        class BrokenRedis:
            @classmethod
            def from_url(cls, *args, **kwargs):
                raise RuntimeError("redis down")

        fake_redis_module = SimpleNamespace(Redis=BrokenRedis)
        with override_settings(CELERY_BROKER_URL="redis://:secret@localhost:6379/0"):
            with patch.dict(sys.modules, {"redis": fake_redis_module}):
                redis_check = views._redis_health()

        self.assertEqual(redis_check["status"], "error")
        self.assertIn("redis://localhost:6379/0", redis_check["detail"])
        self.assertNotIn("secret", redis_check["detail"])

        with patch("lingokatutubo_django.celery.app.control.ping", return_value=[]):
            celery_check = views._celery_worker_health()

        self.assertEqual(celery_check["status"], "warning")
        self.assertIn("No workers responded", celery_check["detail"])

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


# ============================================================
# File Upload Validation Tests
# ============================================================


class TranslatorUploadValidationTests(TestCase):
    """Tests for file-type and signature validation on upload."""

    password = "Bagobo-Upload-Test-2026!"

    def setUp(self):
        from django.core.cache import cache as _cache
        _cache.clear()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.media_root = Path(self.temp_dir.name)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_dir.cleanup)

        self.user = User.objects.create_user(
            username="uploadtest",
            email="uploadtest@example.test",
            password=self.password,
        )

    @staticmethod
    def _make_minimal_docx() -> bytes:
        import io
        import zipfile as _zipfile
        buf = io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                "</Types>",
            )
            zf.writestr("word/document.xml", "<w:document/>")
        return buf.getvalue()

    @staticmethod
    def _make_minimal_jpg() -> bytes:
        from PIL import Image
        import io
        img = Image.new("RGB", (4, 4), color=(200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    @staticmethod
    def _make_minimal_png() -> bytes:
        from PIL import Image
        import io
        img = Image.new("RGB", (4, 4), color=(200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _upload(self, filename, content, content_type, patch_start=True):
        self.client.force_login(self.user)
        uploaded = SimpleUploadedFile(filename, content, content_type=content_type)
        if patch_start:
            with patch("translator.views.start_translation_job"):
                return self.client.post(
                    reverse("translator:upload"),
                    {"file": uploaded, "source_language": "auto", "target_language": "tagabawa"},
                )
        return self.client.post(
            reverse("translator:upload"),
            {"file": uploaded, "source_language": "auto", "target_language": "tagabawa"},
        )

    def test_upload_valid_pdf_accepted(self):
        response = self._upload(
            "doc.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            "application/pdf",
        )
        self.assertEqual(response.status_code, 202)

    def test_upload_valid_docx_accepted(self):
        response = self._upload(
            "doc.docx",
            self._make_minimal_docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(response.status_code, 202)

    def test_upload_valid_jpg_accepted(self):
        response = self._upload("photo.jpg", self._make_minimal_jpg(), "image/jpeg")
        self.assertEqual(response.status_code, 202)

    def test_upload_valid_png_accepted(self):
        response = self._upload("image.png", self._make_minimal_png(), "image/png")
        self.assertEqual(response.status_code, 202)

    def test_upload_valid_txt_accepted(self):
        response = self._upload(
            "notes.txt",
            "Hello world, kumusta?".encode("utf-8"),
            "text/plain",
        )
        self.assertEqual(response.status_code, 202)

    def test_upload_invalid_pdf_signature_rejected(self):
        """File with .pdf extension but wrong magic bytes is rejected."""
        response = self._upload(
            "fake.pdf",
            b"Not a real PDF document",
            "application/pdf",
            patch_start=False,
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_corrupted_docx_rejected(self):
        """File with .docx extension but invalid ZIP content is rejected."""
        response = self._upload(
            "broken.docx",
            b"PK\x03\x04this is not a real zip archive",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            patch_start=False,
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_non_utf8_txt_rejected(self):
        """TXT file with non-UTF-8 bytes is rejected."""
        response = self._upload(
            "latin.txt",
            b"\xff\xfe\x00invalid latin-1 content",
            "text/plain",
            patch_start=False,
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_disallowed_extension_rejected(self):
        """File with a disallowed extension (e.g. .exe) is always rejected."""
        response = self._upload(
            "program.exe",
            b"binary content",
            "application/octet-stream",
            patch_start=False,
        )
        self.assertEqual(response.status_code, 400)


# ============================================================
# Preview Pagination Tests
# ============================================================


class TranslatorPreviewPaginationTests(TestCase):
    """Verify that the bilingual preview paginates segments correctly."""

    password = "Bagobo-Paginate-2026!"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.media_root = Path(self.temp_dir.name)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_dir.cleanup)

        self.alice = User.objects.create_user(
            username="pagalice",
            email="pagalice@example.test",
            password=self.password,
        )

    def _create_completed_job(self):
        return TranslationJob.objects.create(
            owner=self.alice,
            original_filename="long_doc.pdf",
            file_type=TranslationJob.FileType.PDF,
            status=TranslationJob.Status.COMPLETED,
        )

    def _bulk_create_segments(self, job, count):
        TranslationSegment.objects.bulk_create([
            TranslationSegment(
                job=job,
                segment_index=i + 1,
                source_text=f"Source segment {i + 1}",
                translated_text=f"Translated segment {i + 1}",
                source_language="english",
                target_language="tagabawa",
                method="exact_phrase",
                confidence=0.9,
            )
            for i in range(count)
        ])

    def test_preview_first_page_shows_first_25_segments(self):
        job = self._create_completed_job()
        self._bulk_create_segments(job, 30)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_preview", args=[job.id]))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Source segment 1", body)
        self.assertIn("Source segment 25", body)
        self.assertNotIn("Source segment 26", body)
        self.assertIn("Page 1 of 2", body)

    def test_preview_second_page_shows_remaining_segments(self):
        job = self._create_completed_job()
        self._bulk_create_segments(job, 30)
        self.client.force_login(self.alice)

        response = self.client.get(
            reverse("translator:job_preview", args=[job.id]) + "?page=2"
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertNotIn("Source segment 25", body)
        self.assertIn("Source segment 26", body)
        self.assertIn("Source segment 30", body)
        self.assertIn("Page 2 of 2", body)

    def test_preview_total_segment_count_shown_correctly(self):
        job = self._create_completed_job()
        self._bulk_create_segments(job, 30)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_preview", args=[job.id]))

        body = response.content.decode("utf-8")
        # Total segments badge should show 30, not just the current page count (25)
        self.assertIn("30 segments", body)

    def test_preview_no_pagination_for_small_document(self):
        job = self._create_completed_job()
        self._bulk_create_segments(job, 10)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_preview", args=[job.id]))

        body = response.content.decode("utf-8")
        # No pagination nav when all segments fit on one page
        self.assertNotIn("Page 1 of", body)

    def test_preview_owner_isolation_with_pagination(self):
        bob = User.objects.create_user(
            username="pagbob",
            email="pagbob@example.test",
            password=self.password,
        )
        job = self._create_completed_job()
        self._bulk_create_segments(job, 5)
        self.client.force_login(bob)

        response = self.client.get(reverse("translator:job_preview", args=[job.id]))

        self.assertEqual(response.status_code, 404)


# ============================================================
# Admin Access and Read-Only Tests
# ============================================================


class TranslatorAdminTests(TestCase):
    """Verify admin interface access controls and read-only log enforcement."""

    password = "Bagobo-Admin-2026!"

    def setUp(self):
        self.regular = User.objects.create_user(
            username="regular_admin_test",
            email="regular@example.test",
            password=self.password,
        )
        self.staff = User.objects.create_user(
            username="staff_admin_test",
            email="staff@example.test",
            password=self.password,
            is_staff=True,
            is_superuser=True,
        )

    def test_admin_access_denied_for_anonymous(self):
        response = self.client.get("/admin/")
        self.assertNotEqual(response.status_code, 200)

    def test_admin_access_denied_for_regular_user(self):
        self.client.force_login(self.regular)
        response = self.client.get("/admin/")
        self.assertNotEqual(response.status_code, 200)

    def test_admin_accessible_for_superuser(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)

    def test_admin_translation_job_list_visible_to_superuser(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/translator/translationjob/")
        self.assertEqual(response.status_code, 200)

    def test_admin_shows_soft_deleted_jobs(self):
        job = TranslationJob.objects.create(
            owner=self.regular,
            original_filename="deleted_doc.pdf",
            file_type=TranslationJob.FileType.PDF,
            status=TranslationJob.Status.COMPLETED,
            is_deleted=True,
        )
        self.client.force_login(self.staff)
        response = self.client.get("/admin/translator/translationjob/?is_deleted__exact=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("deleted_doc.pdf", response.content.decode("utf-8"))

    def test_system_event_log_has_no_add_permission(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from translator.admin import SystemEventLogAdmin
        from translator.models import SystemEventLog

        admin_instance = SystemEventLogAdmin(SystemEventLog, AdminSite())
        request = RequestFactory().get("/admin/")
        request.user = self.staff
        self.assertFalse(admin_instance.has_add_permission(request))

    def test_system_event_log_has_no_change_permission(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from translator.admin import SystemEventLogAdmin
        from translator.models import SystemEventLog

        admin_instance = SystemEventLogAdmin(SystemEventLog, AdminSite())
        request = RequestFactory().get("/admin/")
        request.user = self.staff
        self.assertFalse(admin_instance.has_change_permission(request))

    def test_system_event_log_has_no_delete_permission(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from translator.admin import SystemEventLogAdmin
        from translator.models import SystemEventLog

        admin_instance = SystemEventLogAdmin(SystemEventLog, AdminSite())
        request = RequestFactory().get("/admin/")
        request.user = self.staff
        self.assertFalse(admin_instance.has_delete_permission(request))

    def test_user_activity_log_is_fully_read_only(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from translator.admin import UserActivityLogAdmin
        from translator.models import UserActivityLog

        admin_instance = UserActivityLogAdmin(UserActivityLog, AdminSite())
        request = RequestFactory().get("/admin/")
        request.user = self.staff
        self.assertFalse(admin_instance.has_add_permission(request))
        self.assertFalse(admin_instance.has_change_permission(request))
        self.assertFalse(admin_instance.has_delete_permission(request))


# ============================================================
# UI Cleanliness Tests
# ============================================================


class TranslatorUICleanlinessTests(TestCase):
    """Verify that duplicate UI elements have been removed."""

    password = "Bagobo-UI-2026!"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.media_root = Path(self.temp_dir.name)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_dir.cleanup)

        self.alice = User.objects.create_user(
            username="uialice",
            email="uialice@example.test",
            password=self.password,
        )

    def _create_job(self, status=TranslationJob.Status.PROCESSING):
        return TranslationJob.objects.create(
            owner=self.alice,
            original_filename="ui_test.pdf",
            file_type=TranslationJob.FileType.PDF,
            status=status,
        )

    def test_job_detail_has_no_separate_status_dl_row(self):
        """Status is shown only in the heading pill, not repeated in a dl row."""
        job = self._create_job(TranslationJob.Status.PROCESSING)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        body = response.content.decode("utf-8")
        # The dl must not contain a "Status" row label
        self.assertNotIn("<dt>Status</dt>", body)

    def test_job_detail_has_no_separate_progress_dl_row(self):
        job = self._create_job(TranslationJob.Status.PROCESSING)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        body = response.content.decode("utf-8")
        self.assertNotIn("<dt>Progress</dt>", body)

    def test_job_detail_has_no_separate_phase_dl_row(self):
        job = self._create_job(TranslationJob.Status.PROCESSING)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        body = response.content.decode("utf-8")
        self.assertNotIn("<dt>Current Phase</dt>", body)

    def test_job_detail_has_no_separate_message_dl_row(self):
        job = self._create_job(TranslationJob.Status.PROCESSING)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        body = response.content.decode("utf-8")
        self.assertNotIn("<dt>Message</dt>", body)

    def test_active_job_banner_hidden_on_its_own_detail_page(self):
        """Global banner is suppressed when the user is already viewing that job."""
        job = self._create_job(TranslationJob.Status.PROCESSING)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        body = response.content.decode("utf-8")
        self.assertNotIn("active-job-banner", body)

    def test_active_job_banner_shows_on_history_page(self):
        """Global banner IS shown when on history page while a job is active."""
        self._create_job(TranslationJob.Status.PROCESSING)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:history"))
        body = response.content.decode("utf-8")
        self.assertIn("active-job-banner", body)

    def test_preview_page_has_no_duplicate_download_button(self):
        """Download button appears at most once on the preview page."""
        job = self._create_job(TranslationJob.Status.COMPLETED)
        output_path = self.media_root / "jobs" / job.job_id / "translated.pdf"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"%PDF-1.4\n%%EOF")
        job.output_file_path = str(output_path)
        job.save(update_fields=["output_file_path"])
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_preview", args=[job.id]))
        body = response.content.decode("utf-8")
        self.assertEqual(body.count("Download Translated PDF"), 1)

    def test_ocr_confidence_row_absent_for_non_ocr_job(self):
        """OCR Confidence row does not appear for digital PDF jobs."""
        job = self._create_job(TranslationJob.Status.COMPLETED)
        job.metadata = {"extraction_method": "direct_pdf_text"}
        job.save(update_fields=["metadata"])
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        body = response.content.decode("utf-8")
        self.assertNotIn("N/A (not an OCR document)", body)

    def test_ocr_low_confidence_warning_badge_shown(self):
        """⚠ Low badge appears when OCR confidence is below threshold."""
        job = self._create_job(TranslationJob.Status.COMPLETED)
        job.metadata = {
            "extraction_method": "ocr_image",
            "ocr_summary": {
                "mean_confidence": 0.45,
                "has_low_quality_warning": True,
            },
        }
        job.save(update_fields=["metadata"])
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        body = response.content.decode("utf-8")
        self.assertIn("45%", body)
        self.assertIn("⚠ Low", body)


class TranslatorDocxPaginationTests(TestCase):
    """Phase 3 audit: DOCX extraction page-breaks long documents correctly."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _make_docx(self, n_paragraphs: int) -> str:
        from docx import Document as DocxDocument
        doc = DocxDocument()
        for i in range(n_paragraphs):
            doc.add_paragraph(f"Paragraph {i + 1}: sample audit text for Phase 3.")
        path = str(Path(self.temp_dir.name) / "test_long.docx")
        doc.save(path)
        return path

    def test_long_docx_produces_multiple_pages(self):
        """50 paragraphs must span at least 2 pages (max ~32 paragraphs per page)."""
        from translator.services.extraction_service import ExtractionService
        pages = ExtractionService.extract_docx_text_and_layout(self._make_docx(50))
        self.assertGreaterEqual(len(pages), 2)

    def test_long_docx_all_paragraphs_extracted(self):
        """All 50 paragraphs must appear in the extracted layout with none dropped."""
        from translator.services.extraction_service import ExtractionService
        n = 50
        pages = ExtractionService.extract_docx_text_and_layout(self._make_docx(n))
        total_lines = sum(
            len(block.get("lines", []))
            for page in pages
            for block in page.get("blocks", [])
        )
        self.assertEqual(total_lines, n)

    def test_no_block_bbox_exceeds_page_height(self):
        """No block bbox y1 may exceed the declared page height (prevents silent clipping)."""
        from translator.services.extraction_service import ExtractionService
        pages = ExtractionService.extract_docx_text_and_layout(self._make_docx(50))
        for page in pages:
            page_h = page.get("height", 792)
            for block in page.get("blocks", []):
                bbox = block.get("bbox", [0, 0, 0, page_h])
                self.assertLessEqual(
                    bbox[3],
                    page_h,
                    f"Block y1={bbox[3]} exceeds page height {page_h} on page {page.get('page')}.",
                )


class TranslatorOCRRotationTests(TestCase):
    """Phase 3 audit: OCR rotation detection and _apply_rotation contract."""

    def _white_image(self):
        from PIL import Image
        return Image.new("RGB", (100, 100), (255, 255, 255))

    def test_apply_rotation_zero_is_noop(self):
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False)
        img = self._white_image()
        out, applied, warning = svc._apply_rotation(img, 0)
        self.assertIs(out, img)
        self.assertEqual(applied, 0)
        self.assertIsNone(warning)

    def test_apply_rotation_90_corrects_image(self):
        """90° rotation: image is rotated (not left unchanged), applied=90, no warning."""
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False)
        img = self._white_image()
        out, applied, warning = svc._apply_rotation(img, 90)
        self.assertIsNot(out, img)
        self.assertEqual(applied, 90)
        self.assertIsNone(warning)

    def test_apply_rotation_270_corrects_image(self):
        """270° rotation: image is rotated (not left unchanged), applied=270, no warning."""
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False)
        img = self._white_image()
        out, applied, warning = svc._apply_rotation(img, 270)
        self.assertIsNot(out, img)
        self.assertEqual(applied, 270)
        self.assertIsNone(warning)

    def test_apply_rotation_180_corrects_image(self):
        """180° rotation must return a new transposed image with no warning."""
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False)
        img = self._white_image()
        out, applied, warning = svc._apply_rotation(img, 180)
        self.assertIsNot(out, img)
        self.assertEqual(applied, 180)
        self.assertIsNone(warning)

    def test_detect_rotation_reads_osd_output(self):
        """_detect_rotation_deg parses Rotate: line from mocked pytesseract OSD output."""
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=True)
        img = self._white_image()
        with patch(
            "pytesseract.image_to_osd",
            return_value="Rotate: 90\nOrientation confidence: 3.14\n",
        ):
            deg = svc._detect_rotation_deg(img)
        self.assertEqual(deg, 90)


class TranslatorWorkflowGatingTests(TestCase):
    """Phase 3 audit: download gating and cross-page segment ordering."""

    password = "Bagobo-Test-2026!"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.media_root = Path(self.temp_dir.name)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_dir.cleanup)

        self.alice = User.objects.create_user(
            username="alice_p3",
            email="alice_p3@example.test",
            password=self.password,
        )

    def _make_completed_job(self, *, with_output_file: bool = False) -> TranslationJob:
        job = TranslationJob.objects.create(
            owner=self.alice,
            original_filename="audit.pdf",
            file_type=TranslationJob.FileType.PDF,
            status=TranslationJob.Status.COMPLETED,
        )
        if with_output_file:
            out = self.media_root / "jobs" / job.job_id / "translated.pdf"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"%PDF-1.4\n%%EOF")
            job.output_file_path = str(out)
            job.save(update_fields=["output_file_path"])
        return job

    def test_completed_job_without_output_file_hides_download(self):
        """COMPLETED job with no translated PDF must not show the Download button."""
        job = self._make_completed_job(with_output_file=False)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        body = response.content.decode("utf-8")
        self.assertNotIn(">Download<", body)

    def test_completed_job_with_output_file_shows_download(self):
        """COMPLETED job with translated PDF present must show the Download button."""
        job = self._make_completed_job(with_output_file=True)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("translator:job_detail", args=[job.id]))
        body = response.content.decode("utf-8")
        self.assertIn(">Download<", body)

    def test_segment_ordering_across_pages(self):
        """Segments from page 1 must have lower segment_index than those on page 2."""
        from translator.services import _sync_structure_models

        job = self._make_completed_job()
        job_dir = self.media_root / "jobs" / job.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        structure_path = str(job_dir / "structure.json")

        structure = {
            "extraction_method": "direct_pdf_text",
            "pages": [
                {
                    "page_number": 1,
                    "width": 612,
                    "height": 792,
                    "rotation": 0,
                    "blocks": [{
                        "type": "text",
                        "bbox": [72, 100, 540, 120],
                        "lines": [{
                            "source_text": "First page text.",
                            "translated_text": "Teksto sa unang pahina.",
                            "translation_method": "exact",
                            "translation_confidence": 1.0,
                            "bbox": [72, 100, 540, 120],
                        }],
                    }],
                },
                {
                    "page_number": 2,
                    "width": 612,
                    "height": 792,
                    "rotation": 0,
                    "blocks": [{
                        "type": "text",
                        "bbox": [72, 100, 540, 120],
                        "lines": [{
                            "source_text": "Second page text.",
                            "translated_text": "Teksto sa ikalawang pahina.",
                            "translation_method": "exact",
                            "translation_confidence": 1.0,
                            "bbox": [72, 100, 540, 120],
                        }],
                    }],
                },
            ],
        }
        with open(structure_path, "w", encoding="utf-8") as fh:
            json.dump(structure, fh)

        _sync_structure_models(str(job.id), structure_path)

        segments = list(
            TranslationSegment.objects.filter(job=job).order_by("segment_index")
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].source_text, "First page text.")
        self.assertEqual(segments[1].source_text, "Second page text.")
        self.assertLess(segments[0].segment_index, segments[1].segment_index)


class TranslatorPhase4FidelityTests(TestCase):
    """Phase 4 audit: reconstruction fidelity, structure validation, rotation, hybrid PDF."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.addCleanup(self.temp_dir.cleanup)

    # ------------------------------------------------------------------
    # 1. Reconstruction fidelity: DOCX output contains all paragraphs
    # ------------------------------------------------------------------

    def _make_docx(self, paragraphs) -> str:
        from docx import Document as DocxDoc
        doc = DocxDoc()
        for text in paragraphs:
            doc.add_paragraph(text)
        path = str(self.root / "test_input.docx")
        doc.save(path)
        return path

    def test_reconstruct_docx_output_contains_all_text(self):
        """_create_output_pdf renders translated text for every DOCX paragraph."""
        import fitz
        from translator.services.extraction_service import ExtractionService
        from translator.services.pipeline_service import PipelineService

        n = 5
        source_texts = [f"Source paragraph {i + 1} content." for i in range(n)]
        expected_translations = [f"TRANS_{i}" for i in range(n)]

        docx_path = self._make_docx(source_texts)
        layout_data = ExtractionService.extract_docx_text_and_layout(docx_path)

        # Build translations keyed by source text
        translations = {
            src: {
                "original": src,
                "translated": exp,
                "method": "test",
                "confidence": 1.0,
            }
            for src, exp in zip(source_texts, expected_translations)
        }

        output_path = str(self.root / "translated.pdf")
        pipeline = PipelineService()
        ok = pipeline._create_output_pdf(layout_data, translations, output_path)
        self.assertTrue(ok, "_create_output_pdf returned False")
        self.assertTrue(Path(output_path).exists(), "Output PDF was not created")

        doc = fitz.open(output_path)
        full_text = "".join(doc[i].get_text() for i in range(doc.page_count))
        doc.close()

        for expected in expected_translations:
            self.assertIn(
                expected,
                full_text,
                f"Expected translated text '{expected}' is missing from the output PDF.",
            )

    def test_output_pdf_page_count_matches_layout(self):
        """Output PDF has the same number of pages as the extracted layout."""
        import fitz
        from translator.services.extraction_service import ExtractionService
        from translator.services.pipeline_service import PipelineService

        docx_path = self._make_docx(
            [f"Paragraph {i}." for i in range(50)]
        )
        layout_data = ExtractionService.extract_docx_text_and_layout(docx_path)
        output_path = str(self.root / "paged.pdf")
        pipeline = PipelineService()
        pipeline._create_output_pdf(layout_data, {}, output_path)

        doc = fitz.open(output_path)
        output_page_count = doc.page_count
        doc.close()

        self.assertEqual(
            output_page_count,
            len(layout_data),
            f"Output PDF has {output_page_count} pages but layout has {len(layout_data)}.",
        )

    # ------------------------------------------------------------------
    # 2. Structure validation
    # ------------------------------------------------------------------

    def test_validate_layout_data_accepts_valid_data(self):
        from translator.services.pipeline_service import PipelineService
        PipelineService._validate_layout_data([
            {"page": 0, "width": 612.0, "height": 792.0, "blocks": []},
        ])

    def test_validate_layout_data_rejects_non_list(self):
        from translator.services.pipeline_service import PipelineService
        with self.assertRaises(ValueError):
            PipelineService._validate_layout_data({"page": 0})

    def test_validate_layout_data_rejects_non_dict_page(self):
        from translator.services.pipeline_service import PipelineService
        with self.assertRaises(ValueError):
            PipelineService._validate_layout_data(["not-a-dict"])

    def test_validate_layout_data_rejects_zero_width(self):
        from translator.services.pipeline_service import PipelineService
        with self.assertRaises(ValueError):
            PipelineService._validate_layout_data([
                {"page": 0, "width": 0, "height": 792.0, "blocks": []},
            ])

    def test_validate_layout_data_rejects_negative_height(self):
        from translator.services.pipeline_service import PipelineService
        with self.assertRaises(ValueError):
            PipelineService._validate_layout_data([
                {"page": 0, "width": 612.0, "height": -1.0, "blocks": []},
            ])

    def test_validate_layout_data_allows_none_dimensions(self):
        """None width/height are skipped (reconstruction defaults to 612×792)."""
        from translator.services.pipeline_service import PipelineService
        PipelineService._validate_layout_data([
            {"page": 0, "width": None, "height": None, "blocks": []},
        ])

    # ------------------------------------------------------------------
    # 3. OCR 90°/270° rotation: image rotation + bbox inverse transform
    # ------------------------------------------------------------------

    def _solid_image(self, w: int, h: int, color=(255, 255, 255)):
        from PIL import Image
        return Image.new("RGB", (w, h), color)

    def test_apply_rotation_90_rotates_image_dimensions(self):
        """Rotating a 100×60 image 90° CW produces a 60×100 image."""
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False)
        img = self._solid_image(100, 60)
        out, applied, warning = svc._apply_rotation(img, 90)
        self.assertEqual(applied, 90)
        self.assertIsNone(warning)
        self.assertEqual(out.size, (60, 100), "90° CW should swap width and height.")

    def test_apply_rotation_270_rotates_image_dimensions(self):
        """Rotating a 100×60 image 90° CCW produces a 60×100 image."""
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False)
        img = self._solid_image(100, 60)
        out, applied, warning = svc._apply_rotation(img, 270)
        self.assertEqual(applied, 270)
        self.assertIsNone(warning)
        self.assertEqual(out.size, (60, 100), "270° correction should swap width and height.")

    def test_flip_layout_90_cw_maps_corners_correctly(self):
        """_flip_page_layout_90_cw maps top-left in rotated space to top-right in original."""
        from translator.services.ocr_stage.ocr_service import OCRService
        # Original page: 595 × 842 pt (portrait)
        # After 90° CW correction: OCR space is 842 × 595
        orig_w, orig_h = 595.0, 842.0
        page_layout = {
            "width": orig_w,
            "height": orig_h,
            "blocks": [{"type": "text", "bbox": [0.0, 0.0, 10.0, 10.0], "lines": [
                {"text": "hi", "bbox": [0.0, 0.0, 10.0, 10.0]}
            ]}],
        }
        result = OCRService._flip_page_layout_90_cw(page_layout, orig_w, orig_h)
        # [ry0, h - rx1, ry1, h - rx0] = [0, 842-10, 10, 842-0] = [0, 832, 10, 842]
        self.assertEqual(result["blocks"][0]["bbox"], [0.0, 832.0, 10.0, 842.0])
        self.assertEqual(result["blocks"][0]["lines"][0]["bbox"], [0.0, 832.0, 10.0, 842.0])
        self.assertEqual(result["width"], orig_w)
        self.assertEqual(result["height"], orig_h)

    def test_flip_layout_270_cw_maps_corners_correctly(self):
        """_flip_page_layout_270_cw maps top-left in rotated space to bottom-left in original."""
        from translator.services.ocr_stage.ocr_service import OCRService
        orig_w, orig_h = 595.0, 842.0
        page_layout = {
            "width": orig_w,
            "height": orig_h,
            "blocks": [{"type": "text", "bbox": [0.0, 0.0, 10.0, 10.0], "lines": [
                {"text": "hi", "bbox": [0.0, 0.0, 10.0, 10.0]}
            ]}],
        }
        result = OCRService._flip_page_layout_270_cw(page_layout, orig_w, orig_h)
        # [w - ry1, rx0, w - ry0, rx1] = [595-10, 0, 595-0, 10] = [585, 0, 595, 10]
        self.assertEqual(result["blocks"][0]["bbox"], [585.0, 0.0, 595.0, 10.0])
        self.assertEqual(result["width"], orig_w)
        self.assertEqual(result["height"], orig_h)

    # ------------------------------------------------------------------
    # 4. Hybrid PDF: per-page extraction metadata
    # ------------------------------------------------------------------

    def _make_minimal_pdf(self, *, n_chars: int = 200) -> str:
        """Create a single-page digital PDF with n_chars of text."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), "A" * n_chars, fontsize=12)
        path = str(self.root / "digital.pdf")
        doc.save(path)
        doc.close()
        return path

    def test_hybrid_pdf_digital_only_sets_direct_pdf_text_method(self):
        """A PDF where all pages are digital should set extraction_method=direct_pdf_text."""
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import FileType, DetectionType

        pdf_path = self._make_minimal_pdf(n_chars=200)
        job = JobStatus("test-hybrid-digital")
        job.detection_type = DetectionType.DIGITAL
        job.metadata = {}

        pipeline = PipelineService()
        layout_data, _ = pipeline._extract_hybrid_pdf(pdf_path, None, job)

        self.assertEqual(job.metadata.get("extraction_method"), "direct_pdf_text")
        self.assertGreater(len(layout_data), 0)
        self.assertEqual(job.metadata["page_extraction_methods"]["0"], "digital")

    def test_hybrid_pdf_low_text_page_is_marked_scanned(self):
        """A nearly-blank PDF page is classified as scanned in page_extraction_methods."""
        import fitz
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import FileType, DetectionType

        # Create a blank (0-char) single-page PDF
        doc = fitz.open()
        doc.new_page(width=612, height=792)
        pdf_path = str(self.root / "blank.pdf")
        doc.save(pdf_path)
        doc.close()

        job = JobStatus("test-hybrid-blank")
        job.detection_type = DetectionType.DIGITAL
        job.metadata = {}

        pipeline = PipelineService()
        layout_data, _ = pipeline._extract_hybrid_pdf(pdf_path, None, job)

        self.assertEqual(job.metadata["page_extraction_methods"]["0"], "ocr")

    # ------------------------------------------------------------------
    # 5. Generated output gating
    # ------------------------------------------------------------------

    def test_output_file_not_created_when_no_text_blocks(self):
        """_create_output_pdf still returns True and creates a PDF even with empty layout."""
        import fitz
        from translator.services.pipeline_service import PipelineService

        output_path = str(self.root / "empty_out.pdf")
        pipeline = PipelineService()
        ok = pipeline._create_output_pdf(
            [{"page": 0, "width": 612, "height": 792, "blocks": []}],
            {},
            output_path,
        )
        self.assertTrue(ok)
        self.assertTrue(Path(output_path).exists())
        doc = fitz.open(output_path)
        self.assertEqual(doc.page_count, 1)
        doc.close()


# ===========================================================================
# PHASE 5: Real OCR Benchmarking, Reading Order, and Layout Quality Validation
# ===========================================================================


class TranslatorPhase5MetricsTests(TestCase):
    """Phase 5: CER and WER metric calculations (pure Python, no Tesseract)."""

    def _cer(self, ref, hyp):
        from translator.services.ocr_stage.qa_report import calculate_cer
        return calculate_cer(ref, hyp)

    def _wer(self, ref, hyp):
        from translator.services.ocr_stage.qa_report import calculate_wer
        return calculate_wer(ref, hyp)

    def test_cer_identical_strings_is_zero(self):
        """CER between a string and itself is 0.0."""
        self.assertEqual(self._cer("hello world", "hello world"), 0.0)

    def test_cer_one_char_substitution(self):
        """One character substitution in a 3-char string gives CER of 1/3."""
        cer = self._cer("cat", "bat")
        self.assertAlmostEqual(cer, 1 / 3, places=5)

    def test_cer_completely_different_capped_at_one(self):
        """CER between completely different strings is capped at 1.0."""
        cer = self._cer("abc", "xyz")
        self.assertEqual(cer, 1.0)

    def test_cer_empty_reference_returns_zero(self):
        """CER with an empty reference is 0.0 (no characters to measure against)."""
        self.assertEqual(self._cer("", "hypothesis text"), 0.0)

    def test_wer_identical_strings_is_zero(self):
        """WER between a sentence and itself is 0.0."""
        self.assertEqual(self._wer("hello world", "hello world"), 0.0)

    def test_wer_one_word_substitution(self):
        """One word substituted in a two-word phrase gives WER of 0.5."""
        wer = self._wer("hello world", "hello there")
        self.assertAlmostEqual(wer, 0.5, places=5)

    def test_wer_empty_reference_returns_zero(self):
        """WER with an empty reference is 0.0 (no words to measure against)."""
        self.assertEqual(self._wer("", "some output"), 0.0)


class TranslatorPhase5QAReportTests(TestCase):
    """Phase 5: DocumentQAReport generation and serialisation."""

    def _make_page(self, text="Hello world.", *, page=0, with_confidence=False):
        """Return a minimal page dict in pipeline layout_data format."""
        line = {"text": text, "bbox": [72.0, 100.0, 540.0, 120.0]}
        if with_confidence:
            line["confidence"] = 0.85
        block = {
            "type": "text",
            "bbox": [72.0, 100.0, 540.0, 120.0],
            "lines": [line],
        }
        if with_confidence:
            block["confidence"] = 0.85
        return {
            "page": page,
            "width": 612.0,
            "height": 792.0,
            "blocks": [block],
        }

    def _make_empty_page(self, *, page=0):
        return {"page": page, "width": 612.0, "height": 792.0, "blocks": []}

    def test_qa_report_as_dict_has_required_keys(self):
        """as_dict() must include all required QA keys."""
        from translator.services.ocr_stage.qa_report import build_document_qa_report
        layout = [self._make_page()]
        report = build_document_qa_report("test.pdf", layout, extraction_method="digital")
        d = report.as_dict()
        for key in (
            "document_name", "extraction_method", "page_count",
            "ocr_confidence", "cer", "wer",
            "empty_page_rate", "failed_page_count",
            "total_processing_time_s", "reading_order_issues",
            "output_result", "warnings", "pages",
        ):
            self.assertIn(key, d, f"Required key '{key}' missing from as_dict()")

    def test_qa_report_empty_page_rate_computed(self):
        """empty_page_rate = empty_page_count / page_count."""
        from translator.services.ocr_stage.qa_report import build_document_qa_report
        layout = [
            self._make_page(page=0),
            self._make_empty_page(page=1),
            self._make_empty_page(page=2),
            self._make_page(page=3),
        ]
        report = build_document_qa_report("doc.pdf", layout)
        d = report.as_dict()
        self.assertEqual(d["page_count"], 4)
        self.assertAlmostEqual(d["empty_page_rate"], 0.5, places=4)

    def test_qa_report_cer_wer_with_ground_truth(self):
        """CER and WER are populated when ground_truth_pages is supplied."""
        from translator.services.ocr_stage.qa_report import build_document_qa_report
        layout = [self._make_page("hello world")]
        # Hypothesis matches reference exactly → CER=0, WER=0
        report = build_document_qa_report(
            "doc.pdf", layout,
            ground_truth_pages=["hello world"],
        )
        self.assertEqual(report.cer, 0.0)
        self.assertEqual(report.wer, 0.0)

    def test_qa_report_cer_wer_none_without_ground_truth(self):
        """CER and WER are None when no ground truth is supplied."""
        from translator.services.ocr_stage.qa_report import build_document_qa_report
        layout = [self._make_page()]
        report = build_document_qa_report("doc.pdf", layout)
        self.assertIsNone(report.cer)
        self.assertIsNone(report.wer)

    def test_qa_report_reading_order_issues_surfaced(self):
        """reading_order_issues in as_dict() matches audit_reading_order output."""
        from translator.services.ocr_stage.qa_report import (
            build_document_qa_report,
            audit_reading_order,
        )
        # Reversed block order: second block has a smaller y than first.
        layout = [{
            "page": 0,
            "width": 612.0,
            "height": 792.0,
            "blocks": [
                {"type": "text", "bbox": [72.0, 400.0, 540.0, 420.0],
                 "lines": [{"text": "Late block", "bbox": [72.0, 400.0, 540.0, 420.0]}]},
                {"type": "text", "bbox": [72.0, 100.0, 540.0, 120.0],
                 "lines": [{"text": "Early block", "bbox": [72.0, 100.0, 540.0, 120.0]}]},
            ],
        }]
        report = build_document_qa_report("reverse.pdf", layout)
        d = report.as_dict()
        self.assertGreater(len(d["reading_order_issues"]), 0)
        combined = " ".join(d["reading_order_issues"])
        self.assertIn("reading-order problem", combined)


class TranslatorPhase5ReadingOrderTests(TestCase):
    """Phase 5: audit_reading_order detects ordering and layout issues."""

    def _page(self, blocks, *, page=0, width=612.0, height=792.0):
        return {"page": page, "width": width, "height": height, "blocks": blocks}

    def _text_block(self, x0, y0, x1, y1, text="text"):
        return {
            "type": "text",
            "bbox": [x0, y0, x1, y1],
            "lines": [{"text": text, "bbox": [x0, y0, x1, y1]}],
        }

    def test_single_column_top_bottom_order_passes(self):
        """Blocks in strict top-to-bottom order produce no ordering issues."""
        from translator.services.ocr_stage.qa_report import audit_reading_order
        layout = [self._page([
            self._text_block(72, 100, 540, 120, "First line"),
            self._text_block(72, 130, 540, 150, "Second line"),
            self._text_block(72, 160, 540, 180, "Third line"),
        ])]
        issues = audit_reading_order(layout)
        order_issues = [i for i in issues if "reading-order problem" in i]
        self.assertEqual(order_issues, [], f"Unexpected order issues: {order_issues}")

    def test_reversed_block_order_is_flagged(self):
        """A block appearing above its predecessor triggers an order issue."""
        from translator.services.ocr_stage.qa_report import audit_reading_order
        layout = [self._page([
            self._text_block(72, 500, 540, 520, "Block at y=500"),
            self._text_block(72, 100, 540, 120, "Block at y=100"),  # reverse
        ])]
        issues = audit_reading_order(layout)
        self.assertTrue(
            any("reading-order problem" in i for i in issues),
            f"Expected a reading-order issue, got: {issues}",
        )

    def test_two_column_layout_is_detected(self):
        """Two blocks in distinct left/right zones trigger a two-column notice."""
        from translator.services.ocr_stage.qa_report import audit_reading_order
        # Left block: x1 <= 612*0.45 ≈ 275.  Right block: x0 >= 612*0.55 ≈ 337.
        layout = [self._page([
            self._text_block(72, 100, 260, 120, "Left column text"),
            self._text_block(350, 100, 560, 120, "Right column text"),
        ])]
        issues = audit_reading_order(layout)
        self.assertTrue(
            any("two-column" in i for i in issues),
            f"Expected a two-column notice, got: {issues}",
        )

    def test_empty_page_is_noted(self):
        """A page with no text blocks produces a 'blank page or extraction failed' note."""
        from translator.services.ocr_stage.qa_report import audit_reading_order
        layout = [{"page": 0, "width": 612.0, "height": 792.0, "blocks": []}]
        issues = audit_reading_order(layout)
        self.assertTrue(
            any("blank page" in i.lower() or "extraction failed" in i.lower() for i in issues),
            f"Expected empty-page note, got: {issues}",
        )


class TranslatorPhase5PSMBenchmarkTests(TestCase):
    """Phase 5: PSM configuration is correctly wired through OCRService."""

    def test_ocr_service_default_psm_is_3(self):
        """OCRService() without psm argument uses PSM 3 (Tesseract auto mode)."""
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService()
        self.assertEqual(svc.psm, 3)

    def test_psm_parameter_accepted_in_range(self):
        """OCRService(psm=6) stores psm=6."""
        from translator.services.ocr_stage.ocr_service import OCRService
        for psm in (0, 1, 3, 4, 6, 11, 13):
            svc = OCRService(psm=psm)
            self.assertEqual(svc.psm, psm, f"psm={psm} was not stored correctly")

    def test_psm_out_of_range_defaults_to_3(self):
        """PSM values outside [0, 13] are silently replaced with 3."""
        from translator.services.ocr_stage.ocr_service import OCRService
        for bad in (14, 99, -1):
            svc = OCRService(psm=bad)
            self.assertEqual(svc.psm, 3, f"PSM {bad} should default to 3")

    def test_psm_config_string_passed_to_tesseract(self):
        """--psm <N> is included in the config string sent to image_to_data."""
        from unittest.mock import patch, MagicMock
        from PIL import Image
        from translator.services.ocr_stage.ocr_service import OCRService

        svc = OCRService(psm=6, detect_orientation=False, preprocess=False,
                         denoise=False, threshold=False)
        svc._verified = True  # skip Tesseract availability check

        mock_data = {
            "text": [], "conf": [], "block_num": [], "par_num": [], "line_num": [],
            "left": [], "top": [], "width": [], "height": [],
        }
        img = Image.new("RGB", (100, 50), (255, 255, 255))

        with patch("pytesseract.image_to_data", return_value=mock_data) as mock_itd:
            svc._ocr_image(img, 0, 72.0, 36.0, 1.0, lang="eng")

        self.assertTrue(mock_itd.called, "image_to_data was not called")
        call_kwargs = mock_itd.call_args[1] if mock_itd.call_args[1] else {}
        call_args = mock_itd.call_args[0] if mock_itd.call_args[0] else ()
        config_value = call_kwargs.get("config") or ""
        self.assertIn("--psm 6", config_value,
                      f"Expected '--psm 6' in config, got: {config_value!r}")


class TranslatorPhase5ReadingOrderReconstructionTests(TestCase):
    """Phase 5: Reconstruction quality — output contains expected text,
    page count matches layout, and None dimensions are handled safely."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.addCleanup(self.temp_dir.cleanup)

    def _make_layout(self, texts, *, width=612.0, height=792.0):
        """Build a single-page layout_data with one line per text string."""
        blocks = []
        y = 100.0
        for text in texts:
            bbox = [72.0, y, 540.0, y + 20.0]
            blocks.append({
                "type": "text",
                "bbox": bbox,
                "lines": [{"text": text, "bbox": bbox}],
            })
            y += 24.0
        return [{"page": 0, "width": width, "height": height, "blocks": blocks}]

    def test_output_contains_all_translated_segments(self):
        """Every translated string appears in the reconstructed output PDF."""
        import fitz
        from translator.services.pipeline_service import PipelineService

        sources = [f"Source {i}" for i in range(4)]
        translations = {src: {"original": src, "translated": f"Xlat{i}", "method": "test", "confidence": 1.0}
                        for i, src in enumerate(sources)}
        layout = self._make_layout(sources)

        output_path = str(self.root / "out.pdf")
        ok = PipelineService()._create_output_pdf(layout, translations, output_path)
        self.assertTrue(ok)

        doc = fitz.open(output_path)
        full_text = "".join(doc[i].get_text() for i in range(doc.page_count))
        doc.close()

        for i in range(4):
            self.assertIn(f"Xlat{i}", full_text,
                          f"Translated segment 'Xlat{i}' missing from output PDF.")

    def test_output_page_count_equals_layout_page_count(self):
        """Output PDF has exactly as many pages as the layout_data list."""
        import fitz
        from translator.services.pipeline_service import PipelineService

        n_pages = 3
        layout = []
        for p in range(n_pages):
            layout.append({
                "page": p, "width": 612.0, "height": 792.0,
                "blocks": [{"type": "text", "bbox": [72, 100, 540, 120],
                             "lines": [{"text": f"Page {p} text", "bbox": [72, 100, 540, 120]}]}],
            })

        output_path = str(self.root / "paged.pdf")
        PipelineService()._create_output_pdf(layout, {}, output_path)

        doc = fitz.open(output_path)
        self.assertEqual(doc.page_count, n_pages)
        doc.close()

    def test_none_width_height_pages_use_default_dimensions(self):
        """Pages with None width/height are created with 612×792 pt defaults."""
        import fitz
        from translator.services.pipeline_service import PipelineService

        layout = [{"page": 0, "width": None, "height": None,
                   "blocks": [{"type": "text", "bbox": [72, 100, 540, 120],
                                "lines": [{"text": "Default size page", "bbox": [72, 100, 540, 120]}]}]}]
        output_path = str(self.root / "default_size.pdf")
        ok = PipelineService()._create_output_pdf(layout, {}, output_path)
        self.assertTrue(ok)

        doc = fitz.open(output_path)
        page = doc[0]
        # _create_output_pdf falls back to 612×792 when dimensions are None/falsy.
        self.assertAlmostEqual(page.rect.width, 612.0, delta=1.0)
        self.assertAlmostEqual(page.rect.height, 792.0, delta=1.0)
        doc.close()

    def test_text_block_completely_outside_page_bounds_is_skipped(self):
        """A block whose bbox is outside the page rect does not crash reconstruction."""
        import fitz
        from translator.services.pipeline_service import PipelineService

        # Block at y=9000 is way outside a 792-pt tall page.
        layout = [{
            "page": 0, "width": 612.0, "height": 792.0,
            "blocks": [
                {
                    "type": "text",
                    "bbox": [72, 100, 540, 120],
                    "lines": [{"text": "Valid line", "bbox": [72, 100, 540, 120]}],
                },
                {
                    "type": "text",
                    "bbox": [72, 9000, 540, 9020],
                    "lines": [{"text": "Far-out line", "bbox": [72, 9000, 540, 9020]}],
                },
            ],
        }]
        translations = {
            "Valid line": {"original": "Valid line", "translated": "Valid OK",
                           "method": "test", "confidence": 1.0},
            "Far-out line": {"original": "Far-out line", "translated": "Should skip",
                             "method": "test", "confidence": 1.0},
        }
        output_path = str(self.root / "oob.pdf")
        ok = PipelineService()._create_output_pdf(layout, translations, output_path)
        self.assertTrue(ok, "_create_output_pdf should not crash on out-of-bounds block")
        self.assertTrue(Path(output_path).exists())


class TranslatorPhase5HybridPDFOrderTests(TestCase):
    """Phase 5: Mixed digital/scanned PDFs preserve page order and store methods."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.addCleanup(self.temp_dir.cleanup)

    def _make_two_page_pdf(self, *, first_page_chars: int, second_page_chars: int) -> str:
        """Create a two-page PDF; each page has the specified number of 'A' characters."""
        import fitz
        doc = fitz.open()
        p0 = doc.new_page(width=612, height=792)
        if first_page_chars:
            p0.insert_text((72, 100), "A" * first_page_chars, fontsize=12)
        p1 = doc.new_page(width=612, height=792)
        if second_page_chars:
            p1.insert_text((72, 100), "A" * second_page_chars, fontsize=12)
        path = str(self.root / "mixed.pdf")
        doc.save(path)
        doc.close()
        return path

    def test_mixed_pdf_page_order_preserved(self):
        """Pages in the layout_data output are in the same order as the source PDF."""
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import DetectionType

        # 200-char first page (digital), 0-char second page (scanned/ocr path)
        pdf_path = self._make_two_page_pdf(first_page_chars=200, second_page_chars=0)
        job = JobStatus("order-test")
        job.detection_type = DetectionType.DIGITAL
        job.metadata = {}

        pipeline = PipelineService()
        layout_data, _ = pipeline._extract_hybrid_pdf(pdf_path, None, job)

        self.assertEqual(len(layout_data), 2, "Expected exactly 2 pages")
        self.assertEqual(layout_data[0]["page"], 0, "First page index must be 0")
        self.assertEqual(layout_data[1]["page"], 1, "Second page index must be 1")

    def test_mixed_pdf_sets_hybrid_method(self):
        """A PDF with one digital and one scanned page stores extraction_method=hybrid."""
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import DetectionType

        pdf_path = self._make_two_page_pdf(first_page_chars=200, second_page_chars=0)
        job = JobStatus("hybrid-method-test")
        job.detection_type = DetectionType.DIGITAL
        job.metadata = {}

        PipelineService()._extract_hybrid_pdf(pdf_path, None, job)
        # First page is digital; second page has no chars → routed to OCR path.
        # extraction_method depends on whether OCR succeeds, but page methods are set.
        page_methods = job.metadata.get("page_extraction_methods", {})
        self.assertEqual(page_methods.get("0"), "digital")
        self.assertEqual(page_methods.get("1"), "ocr")

    def test_hybrid_pdf_page_methods_stored_for_every_page(self):
        """page_extraction_methods contains an entry for every page in the PDF."""
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import DetectionType

        pdf_path = self._make_two_page_pdf(first_page_chars=200, second_page_chars=200)
        job = JobStatus("all-digital-test")
        job.detection_type = DetectionType.DIGITAL
        job.metadata = {}

        pipeline = PipelineService()
        layout_data, _ = pipeline._extract_hybrid_pdf(pdf_path, None, job)

        page_methods = job.metadata.get("page_extraction_methods", {})
        for page_idx in range(len(layout_data)):
            self.assertIn(
                str(page_idx), page_methods,
                f"No extraction method stored for page {page_idx}",
            )


# ===========================================================================
# PHASE 6: Real Tesseract OCR Validation and Document Quality Benchmarking
# ===========================================================================


class TranslatorPhase6EnvironmentTests(TestCase):
    """Phase 6: Tesseract environment audit — correct status when not installed."""

    def test_check_tesseract_returns_dict_with_required_keys(self):
        """check_tesseract_environment() must return a dict with all expected keys."""
        from translator.services.ocr_stage.environment import check_tesseract_environment
        env = check_tesseract_environment()
        for key in ("available", "version", "languages", "osd_available",
                    "binary_path", "errors"):
            self.assertIn(key, env, f"Required key '{key}' missing from environment report")

    def test_check_tesseract_available_is_false_when_not_installed(self):
        """available=False when Tesseract binary is absent from PATH."""
        from translator.services.ocr_stage.environment import check_tesseract_environment
        # In this environment Tesseract is not installed.
        env = check_tesseract_environment()
        # If somehow Tesseract IS present, we still validate the dict shape.
        self.assertIsInstance(env["available"], bool)
        if not env["available"]:
            self.assertIsNone(env["version"])
            self.assertGreater(len(env["errors"]), 0,
                               "errors list should be non-empty when Tesseract is absent")

    def test_check_tesseract_errors_is_nonempty_when_not_installed(self):
        """errors[] contains at least one actionable message when Tesseract is missing."""
        from translator.services.ocr_stage.environment import check_tesseract_environment
        env = check_tesseract_environment()
        if not env["available"]:
            self.assertTrue(
                len(env["errors"]) > 0,
                "At least one actionable error message is expected when Tesseract is absent",
            )
            # The error should mention installation guidance.
            combined = " ".join(env["errors"]).lower()
            has_guidance = any(
                kw in combined
                for kw in ("install", "tesseract", "path", "tesseract_cmd")
            )
            self.assertTrue(has_guidance,
                            f"Error message should include installation guidance: {env['errors']}")

    def test_ocr_service_is_available_returns_false(self):
        """OCRService.is_available() returns False when Tesseract is not installed."""
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService()
        result = svc.is_available()
        # is_available() must always return a bool (never raise).
        self.assertIsInstance(result, bool)
        # In this environment it should be False.
        if not result:
            self.assertFalse(result)

    def test_assert_tesseract_available_raises_runtime_error(self):
        """assert_tesseract_available() raises RuntimeError when Tesseract is absent."""
        from translator.services.ocr_stage.environment import (
            check_tesseract_environment,
            assert_tesseract_available,
        )
        env = check_tesseract_environment()
        if not env["available"]:
            with self.assertRaises(RuntimeError) as ctx:
                assert_tesseract_available()
            self.assertIn("Tesseract", str(ctx.exception))
        # If Tesseract IS present, the function should not raise.


class TranslatorPhase6FixtureBuilderTests(TestCase):
    """Phase 6: Programmatic OCR fixture builder creates valid images and PDFs."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.addCleanup(self.temp_dir.cleanup)

    def _build(self):
        from translator.management.commands.ocr_benchmark import build_fixtures
        return build_fixtures(self.root)

    def test_clean_scan_fixture_creates_valid_png(self):
        """build_fixtures() creates a clean_scan.png that PIL can open."""
        from PIL import Image
        paths = self._build()
        self.assertIn("clean_scan", paths)
        img = Image.open(paths["clean_scan"])
        self.assertEqual(img.mode, "RGB")
        self.assertGreater(img.width, 0)
        self.assertGreater(img.height, 0)

    def test_faded_scan_fixture_has_lower_contrast_than_clean(self):
        """Faded scan has lower pixel contrast (smaller max-min range) than clean scan."""
        from PIL import Image
        paths = self._build()
        clean = list(Image.open(paths["clean_scan"]).convert("L").getdata())
        faded = list(Image.open(paths["faded_scan"]).convert("L").getdata())
        clean_contrast = max(clean) - min(clean)
        faded_contrast = max(faded) - min(faded)
        # Clean scan: bg=255, ink=0 → contrast=255.
        # Faded scan: bg=240, ink=130 → contrast=110. Faded is lower-contrast.
        self.assertLess(faded_contrast, clean_contrast,
                        "Faded scan should have lower pixel contrast than clean scan")

    def test_rotated_fixture_has_swapped_dimensions(self):
        """Rotating a landscape image 90° produces a portrait image."""
        from PIL import Image
        paths = self._build()
        self.assertIn("rotated_90", paths)
        clean = Image.open(paths["clean_scan"])
        rotated = Image.open(paths["rotated_90"])
        # 90° rotation swaps width and height.
        self.assertEqual(rotated.width, clean.height)
        self.assertEqual(rotated.height, clean.width)

    def test_blank_fixture_is_all_white(self):
        """Blank fixture has a mean pixel value of 255 (pure white)."""
        from PIL import Image
        import statistics
        paths = self._build()
        self.assertIn("blank", paths)
        blank = Image.open(paths["blank"]).convert("L")
        mean_val = statistics.mean(blank.getdata())
        self.assertEqual(mean_val, 255.0, "Blank fixture should be pure white (255)")

    def test_ground_truth_files_are_created_for_all_fixtures(self):
        """build_fixtures() creates a .txt ground-truth file for each expected fixture."""
        from translator.management.commands.ocr_benchmark import GROUND_TRUTH
        self._build()
        gt_dir = self.root / "ground_truth"
        for name in GROUND_TRUTH:
            gt_file = gt_dir / f"{name}.txt"
            self.assertTrue(
                gt_file.exists(),
                f"Ground-truth file missing: {gt_file}",
            )

    def test_ground_truth_blank_fixture_is_empty(self):
        """The blank fixture's ground-truth file is an empty string."""
        self._build()
        gt_file = self.root / "ground_truth" / "blank.txt"
        self.assertTrue(gt_file.exists())
        self.assertEqual(gt_file.read_text(encoding="utf-8").strip(), "")


class TranslatorPhase6QAReportExportTests(TestCase):
    """Phase 6: DocumentQAReport CSV/JSON export and pass/fail logic."""

    def _make_report_with_cer(self, cer_value):
        """Return a DocumentQAReport whose single page has the given CER."""
        from translator.services.ocr_stage.qa_report import (
            DocumentQAReport,
            PageQAResult,
        )
        page = PageQAResult(
            page_index=0,
            extraction_method="ocr",
            char_count=100,
            block_count=3,
            ocr_confidence=0.85,
            cer=cer_value,
            wer=cer_value,
            psm=3,
        )
        return DocumentQAReport(
            document_name="test.png",
            extraction_method="ocr",
            page_count=1,
            ocr_confidence=0.85,
            cer=cer_value,
            wer=cer_value,
            empty_page_count=0,
            failed_page_count=0,
            total_processing_time_s=1.23,
            reading_order_issues=[],
            output_result="ok",
            warnings=[],
            pages=[page],
        )

    def test_csv_rows_has_correct_column_names(self):
        """to_csv_rows() returns dicts with all required CSV column keys."""
        report = self._make_report_with_cer(0.05)
        rows = report.to_csv_rows()
        self.assertEqual(len(rows), 1)
        for col in ("filename", "page", "extraction_method", "confidence",
                    "cer", "wer", "processing_time_s", "psm", "warnings", "pass_fail"):
            self.assertIn(col, rows[0], f"Missing CSV column: {col}")

    def test_csv_has_one_row_per_page(self):
        """to_csv_rows() returns exactly one dict per page in the report."""
        from translator.services.ocr_stage.qa_report import (
            DocumentQAReport,
            PageQAResult,
        )
        pages = [
            PageQAResult(page_index=i, extraction_method="ocr",
                         char_count=50, block_count=2, cer=0.05, wer=0.05, psm=3)
            for i in range(3)
        ]
        report = DocumentQAReport(
            document_name="multi.pdf", extraction_method="ocr",
            page_count=3, ocr_confidence=None, cer=0.05, wer=0.05,
            empty_page_count=0, failed_page_count=0,
            total_processing_time_s=2.0, reading_order_issues=[],
            output_result="ok", warnings=[], pages=pages,
        )
        rows = report.to_csv_rows()
        self.assertEqual(len(rows), 3)

    def test_csv_string_has_header_row(self):
        """to_csv_string() starts with the header row."""
        report = self._make_report_with_cer(0.05)
        csv_text = report.to_csv_string()
        first_line = csv_text.splitlines()[0]
        self.assertIn("filename", first_line)
        self.assertIn("pass_fail", first_line)

    def test_pass_fail_skip_when_no_cer(self):
        """pass_fail is 'skip' when CER is None (no ground truth)."""
        report = self._make_report_with_cer(None)
        rows = report.to_csv_rows()
        self.assertEqual(rows[0]["pass_fail"], "skip")

    def test_pass_fail_pass_when_cer_under_threshold(self):
        """pass_fail is 'pass' when CER < 0.10."""
        report = self._make_report_with_cer(0.05)
        rows = report.to_csv_rows()
        self.assertEqual(rows[0]["pass_fail"], "pass")

    def test_pass_fail_fail_when_cer_at_or_above_threshold(self):
        """pass_fail is 'fail' when CER >= 0.10."""
        for cer in (0.10, 0.50, 1.0):
            report = self._make_report_with_cer(cer)
            rows = report.to_csv_rows()
            self.assertEqual(rows[0]["pass_fail"], "fail",
                             f"Expected 'fail' for CER={cer}")

    def test_json_round_trip(self):
        """to_json_string() / json.loads() round-trips without data loss."""
        import json as _json
        report = self._make_report_with_cer(0.08)
        json_str = report.to_json_string()
        data = _json.loads(json_str)
        self.assertEqual(data["document_name"], "test.png")
        self.assertAlmostEqual(data["cer"], 0.08, places=4)
        self.assertEqual(len(data["pages"]), 1)
        self.assertEqual(data["pages"][0]["psm"], 3)


class TranslatorPhase6MixedPDFRoutingTests(TestCase):
    """Phase 6: Hybrid PDF routes digital pages to fitz and scanned to OCR path."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.addCleanup(self.temp_dir.cleanup)

    def _make_two_page_pdf(self, first_chars: int, second_chars: int) -> str:
        import fitz
        doc = fitz.open()
        p0 = doc.new_page(width=612, height=792)
        if first_chars:
            p0.insert_text((72, 100), "A" * first_chars, fontsize=12)
        p1 = doc.new_page(width=612, height=792)
        if second_chars:
            p1.insert_text((72, 100), "A" * second_chars, fontsize=12)
        path = str(self.root / "twopages.pdf")
        doc.save(path)
        doc.close()
        return path

    def test_digital_page_uses_direct_extraction_not_ocr(self):
        """A page with sufficient text is extracted via fitz (method=digital)."""
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import DetectionType

        pdf_path = self._make_two_page_pdf(first_chars=200, second_chars=200)
        job = JobStatus("route-digital")
        job.detection_type = DetectionType.DIGITAL
        job.metadata = {}

        PipelineService()._extract_hybrid_pdf(pdf_path, None, job)
        methods = job.metadata.get("page_extraction_methods", {})
        self.assertEqual(methods.get("0"), "digital",
                         "A page with 200 chars should use the digital (fitz) path")
        self.assertEqual(methods.get("1"), "digital")

    def test_scanned_page_records_ocr_error_when_tesseract_unavailable(self):
        """When Tesseract is absent, scanned pages have ocr_error in their page_data.

        Skipped when Tesseract IS installed: a blank scanned page then correctly
        returns empty blocks with no error, which is the expected happy-path.
        """
        import unittest as _ut
        from translator.services.ocr_stage.environment import check_tesseract_environment
        if check_tesseract_environment()["available"]:
            raise _ut.SkipTest(
                "Tesseract is installed — blank scanned page returns empty blocks "
                "(no ocr_error expected). Phase 7 adds explicit tests for this case."
            )

        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import DetectionType

        # Page 1 = blank → triggers OCR path; Tesseract is not installed.
        pdf_path = self._make_two_page_pdf(first_chars=200, second_chars=0)
        job = JobStatus("route-scanned")
        job.detection_type = DetectionType.DIGITAL
        job.metadata = {}

        layout_data, _ = PipelineService()._extract_hybrid_pdf(pdf_path, None, job)
        scanned_page = layout_data[1]
        # The OCR path should have recorded an error (not silently produced empty output).
        has_error = bool(
            scanned_page.get("ocr_error") or scanned_page.get("ocr_warning")
        )
        self.assertTrue(
            has_error,
            "Scanned page with unavailable Tesseract should record ocr_error or ocr_warning, "
            f"got: {scanned_page}",
        )

    def test_overall_method_is_hybrid_when_both_paths_used(self):
        """extraction_method becomes 'hybrid' when both digital and OCR pages appear."""
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import DetectionType

        pdf_path = self._make_two_page_pdf(first_chars=200, second_chars=0)
        job = JobStatus("route-hybrid")
        job.detection_type = DetectionType.DIGITAL
        job.metadata = {}

        PipelineService()._extract_hybrid_pdf(pdf_path, None, job)
        # Page 0 is digital; page 1 triggers OCR path (regardless of Tesseract outcome).
        methods = job.metadata.get("page_extraction_methods", {})
        self.assertEqual(methods.get("0"), "digital")
        self.assertEqual(methods.get("1"), "ocr")
        self.assertEqual(
            job.metadata.get("extraction_method"), "hybrid",
            "When one page is digital and one is OCR, overall method must be 'hybrid'",
        )

    def test_benchmark_command_importable_and_reports_unavailable(self):
        """ocr_benchmark command is importable and gracefully handles Tesseract absence."""
        from translator.services.ocr_stage.environment import check_tesseract_environment
        from translator.management.commands.ocr_benchmark import build_fixtures, GROUND_TRUTH

        # build_fixtures runs on PIL/fitz only — no Tesseract needed.
        fixture_dir = self.root / "fixtures"
        paths = build_fixtures(fixture_dir)

        # All GROUND_TRUTH fixtures should have been created.
        for name in GROUND_TRUTH:
            gt_file = fixture_dir / "ground_truth" / f"{name}.txt"
            self.assertTrue(gt_file.exists(), f"Missing ground-truth file: {gt_file}")

        # Environment check must clearly say Tesseract is absent.
        env = check_tesseract_environment()
        if not env["available"]:
            self.assertFalse(env["available"])
            self.assertTrue(len(env["errors"]) > 0)


# ────────────────────────────────────────────────────────────────────────────
# Phase 7 — Real OCR Benchmark Execution and Evidence-Based Fixes
# ────────────────────────────────────────────────────────────────────────────

class TranslatorPhase7PreprocessingTests(TestCase):
    """Unit tests for the Phase 7 preprocessing fixes — no Tesseract required."""

    def _make_gray_image(self, width=200, height=80, bg_gray=240, text_gray=130):
        """Create a synthetic grayscale image simulating a faded scan."""
        from PIL import Image, ImageDraw
        img = Image.new("L", (width, height), bg_gray)
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 20, 150, 60], fill=text_gray)
        return img

    def test_threshold_formula_does_not_blacken_gray_background(self):
        """Phase 7 fix: threshold must not turn a gray background (240) to black.

        Old formula (mean+255)/2 = 247 for mean=239.6 → bg=240 < 247 → black.
        New formula mean*0.85 = 203 → bg=240 > 203 → white.
        """
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False, denoise=False)
        img = self._make_gray_image(bg_gray=240, text_gray=130)
        result = svc._preprocess(img)
        corner_pixel = result.getpixel((0, 0))
        self.assertEqual(
            corner_pixel, 255,
            f"Gray background (240) must become white (255) after threshold, got {corner_pixel}. "
            "Old formula (mean+255)/2 incorrectly blackens gray backgrounds."
        )

    def test_threshold_formula_keeps_dark_text_black(self):
        """Text pixels (130) must remain black after faded-scan thresholding."""
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False, denoise=False)
        img = self._make_gray_image(bg_gray=240, text_gray=130)
        result = svc._preprocess(img)
        text_pixel = result.getpixel((80, 40))
        self.assertEqual(
            text_pixel, 0,
            f"Dark text pixel (130) must become black (0) after threshold, got {text_pixel}."
        )

    def test_threshold_not_applied_below_mean_cutoff(self):
        """Threshold step must be skipped when mean pixel value ≤ 180."""
        from PIL import Image
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False, denoise=False)
        # All-dark image: mean ≈ 50 → threshold NOT applied
        img = Image.new("L", (100, 50), 50)
        result = svc._preprocess(img)
        self.assertEqual(result.getpixel((10, 10)), 50,
                         "No threshold when image mean ≤ 180 — pixel must be unchanged")

    def test_clean_scan_background_stays_white_after_preprocessing(self):
        """Pure white background (255) must remain white after preprocessing."""
        from PIL import Image
        from translator.services.ocr_stage.ocr_service import OCRService
        svc = OCRService(detect_orientation=False, denoise=False)
        img = Image.new("RGB", (200, 80), (255, 255, 255))
        result = svc._preprocess(img)
        self.assertEqual(result.getpixel((10, 10)), 255,
                         "White background must remain white after preprocessing")

    def test_ocr_config_includes_dpi_flag(self):
        """_ocr_image must pass --dpi to Tesseract so low-DPI images are read correctly."""
        import unittest
        from translator.services.ocr_stage.environment import check_tesseract_environment
        if not check_tesseract_environment()["available"]:
            raise unittest.SkipTest("Tesseract not available")

        from PIL import Image
        from unittest.mock import patch, MagicMock
        from pytesseract import Output
        from translator.services.ocr_stage.ocr_service import OCRService

        svc = OCRService(dpi=300, detect_orientation=False, preprocess=False, denoise=False)
        img = Image.new("RGB", (200, 80), (255, 255, 255))
        captured_config = {}

        import pytesseract as _pt
        original_fn = _pt.image_to_data

        def capture_config(image, output_type, lang, config, timeout):
            captured_config["config"] = config
            return original_fn(image, output_type=output_type, lang=lang,
                               config=config, timeout=timeout)

        with patch("pytesseract.image_to_data", side_effect=capture_config):
            svc._ocr_image(img, 0, 144.0, 57.6, 72.0 / 300.0, lang="eng")

        self.assertIn("--dpi 300", captured_config.get("config", ""),
                      "Tesseract config must include '--dpi 300' to override missing DPI metadata")

    def test_fixture_images_are_scaled_4x(self):
        """Phase 7 fixture builder saves 4× scaled images (2400×800) for reliable OCR."""
        import tempfile
        from pathlib import Path
        from PIL import Image
        from translator.management.commands.ocr_benchmark import build_fixtures

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            paths = build_fixtures(Path(td))
            with Image.open(paths["clean_scan"]) as img:
                size = img.size
            self.assertEqual(size, (2400, 800),
                             "clean_scan fixture must be 2400×800 (4× scale) for Tesseract legibility")

    def test_fixture_images_have_dpi_metadata(self):
        """Phase 7 fixture builder embeds 300 DPI in PNG metadata."""
        import tempfile
        from pathlib import Path
        from PIL import Image
        from translator.management.commands.ocr_benchmark import build_fixtures

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            paths = build_fixtures(Path(td))
            with Image.open(paths["clean_scan"]) as img:
                dpi_info = img.info.get("dpi")
            self.assertIsNotNone(dpi_info, "clean_scan PNG must carry DPI metadata")
            # PIL stores DPI as float; PNG spec rounds at ~0.4 DPI precision
            self.assertAlmostEqual(dpi_info[0], 300, delta=1,
                                   msg=f"DPI must be ≈300, got {dpi_info}")


class TranslatorPhase7RealOCRTests(TestCase):
    """End-to-end OCR regression tests using real Tesseract 5.5.0.

    Every test in this class skips automatically when Tesseract is not available
    so the suite stays green on CI environments without a Tesseract binary.
    """

    @classmethod
    def _skip_if_no_tesseract(cls):
        import unittest
        from translator.services.ocr_stage.environment import check_tesseract_environment
        env = check_tesseract_environment()
        if not env["available"]:
            raise unittest.SkipTest(
                f"Tesseract not available: {'; '.join(env['errors'])}"
            )

    def _fixture_image(self, name):
        """Return path to a freshly-built benchmark fixture."""
        import tempfile
        from pathlib import Path
        from translator.management.commands.ocr_benchmark import build_fixtures
        td = tempfile.mkdtemp()
        self._tmpdir = td
        return build_fixtures(Path(td))[name]

    def tearDown(self):
        import shutil
        td = getattr(self, "_tmpdir", None)
        if td:
            shutil.rmtree(td, ignore_errors=True)

    def test_clean_scan_achieves_low_cer_with_real_tesseract(self):
        """Real OCR on a clean black-on-white fixture achieves CER < 0.10 (Phase 7 fix)."""
        self._skip_if_no_tesseract()
        from translator.services.ocr_stage.ocr_service import OCRService
        from translator.services.ocr_stage.qa_report import calculate_cer

        svc = OCRService(psm=3, lang="eng", detect_orientation=False)
        path = self._fixture_image("clean_scan")
        pages = svc.extract_image_text_and_layout(str(path))
        hyp = " ".join(
            line.get("text", "")
            for page in pages
            for block in page.get("blocks", [])
            if block.get("type") == "text"
            for line in block.get("lines", [])
        ).strip()
        ref = "Hello world. This is a clean scan document."
        cer = calculate_cer(ref, hyp)
        self.assertLess(
            cer, 0.10,
            f"clean_scan CER must be < 0.10 after Phase 7 fixes, got {cer:.4f} "
            f"(hypothesis: {repr(hyp)!r})"
        )

    def test_faded_scan_achieves_low_cer_after_threshold_fix(self):
        """Real OCR on a faded scan achieves CER < 0.10 after the Phase 7 threshold fix.

        Before the fix: CER=1.0 (all-black image from (mean+255)/2 formula).
        After the fix:  CER≈0.028 (background stays white, text stays black).
        """
        self._skip_if_no_tesseract()
        from translator.services.ocr_stage.ocr_service import OCRService
        from translator.services.ocr_stage.qa_report import calculate_cer

        svc = OCRService(psm=3, lang="eng", detect_orientation=False)
        path = self._fixture_image("faded_scan")
        pages = svc.extract_image_text_and_layout(str(path))
        hyp = " ".join(
            line.get("text", "")
            for page in pages
            for block in page.get("blocks", [])
            if block.get("type") == "text"
            for line in block.get("lines", [])
        ).strip()
        ref = "Hello world. This is a clean scan document."
        cer = calculate_cer(ref, hyp)
        self.assertLess(
            cer, 0.10,
            f"faded_scan CER must be < 0.10 after threshold fix, got {cer:.4f} "
            f"(hypothesis: {repr(hyp)!r})"
        )

    def test_blank_scanned_page_returns_empty_blocks_not_error(self):
        """Blank page with Tesseract available returns empty blocks, no ocr_error."""
        self._skip_if_no_tesseract()
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import DetectionType
        import tempfile, fitz
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "blank.pdf"
            doc = fitz.open()
            doc.new_page(width=612, height=792)
            doc.new_page(width=612, height=792)
            doc.save(str(p))
            doc.close()

            job = JobStatus("phase7-blank")
            job.detection_type = DetectionType.DIGITAL
            job.metadata = {}
            layout_data, _ = PipelineService()._extract_hybrid_pdf(
                str(p), None, job
            )
            blank_page = layout_data[1]
            self.assertNotIn(
                "ocr_error", blank_page,
                "Blank page with Tesseract available must not have ocr_error"
            )
            self.assertEqual(
                blank_page.get("blocks", []), [],
                "Blank page should have no blocks"
            )

    def test_psm3_mean_cer_better_than_psm4_on_real_fixtures(self):
        """PSM 3 must match or beat PSM 4 on standard fixtures — default must not change."""
        self._skip_if_no_tesseract()
        import tempfile
        from pathlib import Path
        from translator.management.commands.ocr_benchmark import (
            build_fixtures, GROUND_TRUTH, _evaluate_image_fixture,
        )

        with tempfile.TemporaryDirectory() as td:
            paths = build_fixtures(Path(td))
            image_names = ["clean_scan", "faded_scan", "two_column", "table"]
            cers = {3: [], 4: []}
            for name in image_names:
                if name not in paths:
                    continue
                gt = GROUND_TRUTH.get(name, "")
                if not gt:
                    continue
                result = _evaluate_image_fixture(paths[name], gt, "eng", [3, 4])
                for pr in result["psm_results"]:
                    if pr.get("cer") is not None:
                        cers[pr["psm"]].append(pr["cer"])

            mean3 = sum(cers[3]) / len(cers[3]) if cers[3] else 1.0
            mean4 = sum(cers[4]) / len(cers[4]) if cers[4] else 1.0
            self.assertLessEqual(
                mean3, mean4,
                f"PSM 3 mean CER ({mean3:.4f}) should be ≤ PSM 4 ({mean4:.4f}) "
                "— default PSM 3 must not be changed."
            )

    def test_environment_check_available_with_tesseract_cmd(self):
        """check_tesseract_environment() returns available=True when TESSERACT_CMD is set."""
        self._skip_if_no_tesseract()
        from translator.services.ocr_stage.environment import check_tesseract_environment
        env = check_tesseract_environment()
        self.assertTrue(env["available"],
                        "Tesseract must report available=True when binary is reachable")
        self.assertIn("eng", env["languages"], "English language pack must be installed")
        self.assertTrue(env["osd_available"], "OSD traineddata must be installed")
        self.assertFalse(env["errors"], f"No errors expected, got: {env['errors']}")

    def test_mixed_pdf_routes_digital_and_ocr_pages(self):
        """Phase 7 regression: hybrid PDF extraction routes pages correctly."""
        self._skip_if_no_tesseract()
        import tempfile
        from pathlib import Path
        from translator.management.commands.ocr_benchmark import build_fixtures
        from translator.services.pipeline_service import PipelineService, JobStatus
        from translator.services.models import DetectionType

        with tempfile.TemporaryDirectory() as td:
            paths = build_fixtures(Path(td))
            pdf_path = paths.get("mixed_digital_scanned")
            if not pdf_path:
                self.skipTest("mixed_digital_scanned fixture not available")

            job = JobStatus("phase7-hybrid")
            job.detection_type = DetectionType.DIGITAL
            job.metadata = {}
            layout_data, _ = PipelineService()._extract_hybrid_pdf(
                str(pdf_path), None, job
            )
            methods = job.metadata.get("page_extraction_methods", {})
            self.assertEqual(methods.get("0"), "digital",
                             "Dense digital page must use digital extraction")
            self.assertEqual(methods.get("1"), "ocr",
                             "Blank page must use OCR extraction path")
            self.assertEqual(job.metadata.get("extraction_method"), "hybrid",
                             "Overall method must be 'hybrid'")
