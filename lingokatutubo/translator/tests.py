import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

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
        )
        self.client.force_login(self.alice)

        response = self.client.get(reverse("translator:job_preview", args=[job.id]))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Original from database", body)
        self.assertIn("Translated from database", body)
        self.assertIn("phrasebook_exact", body)

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
