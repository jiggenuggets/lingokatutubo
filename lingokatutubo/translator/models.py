import uuid

from django.conf import settings
from django.db import models


class TranslationJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class FileType(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "DOCX"
        JPG = "jpg", "JPG"
        PNG = "png", "PNG"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="translation_jobs",
        on_delete=models.CASCADE,
    )
    original_filename = models.CharField(max_length=255)
    upload_file_path = models.TextField(blank=True)
    file_type = models.CharField(max_length=16, choices=FileType.choices)
    detection_type = models.CharField(max_length=32, blank=True)
    source_language = models.CharField(max_length=32, default="auto")
    target_language = models.CharField(max_length=32, default="tagabawa")
    ocr_languages = models.CharField(max_length=128, blank=True)

    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    current_phase = models.CharField(max_length=64, default="queued")
    current_step = models.CharField(max_length=160, default="Queued for processing")
    phase_message = models.TextField(blank=True)
    error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    input_file_path = models.TextField(blank=True)
    output_file_path = models.TextField(blank=True)
    bilingual_file_path = models.TextField(blank=True)
    structure_file_path = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.status})"

    @property
    def job_id(self) -> str:
        return str(self.id)
