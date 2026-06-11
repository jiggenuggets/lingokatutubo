import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import TranslationJob


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
        start_translation.assert_called_once()
        self.assertEqual(start_translation.call_args.args[0].id, job.id)

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

    def _create_job(self, owner, status=TranslationJob.Status.QUEUED):
        return TranslationJob.objects.create(
            owner=owner,
            original_filename="sample.pdf",
            file_type=TranslationJob.FileType.PDF,
            status=status,
        )

    def _write_job_file(self, job, *parts_and_content):
        *parts, content = parts_and_content
        path = self.media_root.joinpath("jobs", job.job_id, *parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path
