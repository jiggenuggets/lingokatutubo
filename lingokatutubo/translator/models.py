import uuid

from django.conf import settings
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Language(TimestampedModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    autonym = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class LanguagePair(TimestampedModel):
    source_language = models.ForeignKey(
        Language,
        related_name="source_pairs",
        on_delete=models.PROTECT,
    )
    target_language = models.ForeignKey(
        Language,
        related_name="target_pairs",
        on_delete=models.PROTECT,
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["source_language__name", "target_language__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_language", "target_language"],
                name="translator_unique_language_pair",
            ),
            models.CheckConstraint(
                check=~models.Q(source_language=models.F("target_language")),
                name="translator_language_pair_not_identity",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_language.code} -> {self.target_language.code}"


class TranslationJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RETRYING = "retrying", "Retrying"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class FileType(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "DOCX"
        JPG = "jpg", "JPG"
        PNG = "png", "PNG"
        TXT = "txt", "Text"

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

    # Soft-delete fields — deleted jobs are hidden from normal history queries
    # but preserved in the database for audit and recovery purposes.
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["owner", "is_deleted", "-created_at"], name="trans_owner_del_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.status})"

    @property
    def job_id(self) -> str:
        return str(self.id)


class UploadedDocument(TimestampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="uploaded_documents",
        on_delete=models.CASCADE,
    )
    job = models.OneToOneField(
        TranslationJob,
        related_name="uploaded_document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    original_filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=16, choices=TranslationJob.FileType.choices)
    file_path = models.TextField()
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["file_type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.original_filename


class DocumentPage(TimestampedModel):
    job = models.ForeignKey(
        TranslationJob,
        related_name="pages",
        on_delete=models.CASCADE,
    )
    page_number = models.PositiveIntegerField()
    width = models.FloatField(null=True, blank=True)
    height = models.FloatField(null=True, blank=True)
    rotation = models.IntegerField(default=0)
    source_image_path = models.TextField(blank=True)
    extracted_text = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["job", "page_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "page_number"],
                name="translator_unique_page_per_job",
            )
        ]

    def __str__(self) -> str:
        return f"{self.job_id} page {self.page_number}"


class OCRResult(TimestampedModel):
    class Status(models.TextChoices):
        PENDING_REVIEW = "pending_review", "Pending review"
        ACCEPTED = "accepted", "Accepted"
        CORRECTED = "corrected", "Corrected"
        REJECTED = "rejected", "Rejected"

    job = models.ForeignKey(
        TranslationJob,
        related_name="ocr_results",
        on_delete=models.CASCADE,
    )
    page = models.ForeignKey(
        DocumentPage,
        related_name="ocr_results",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    engine = models.CharField(max_length=80, default="tesseract")
    language_codes = models.CharField(max_length=128, blank=True)
    text = models.TextField(blank=True)
    confidence = models.FloatField(null=True, blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING_REVIEW,
        db_index=True,
    )
    warnings = models.JSONField(default=list, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="reviewed_ocr_results",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["job", "page__page_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "page", "engine"],
                name="translator_unique_ocr_result_per_page_engine",
            )
        ]
        indexes = [
            models.Index(fields=["job", "status"]),
            models.Index(fields=["engine", "status"]),
        ]

    def __str__(self) -> str:
        return f"OCR {self.engine} for {self.job_id}"


class TranslationSegment(TimestampedModel):
    job = models.ForeignKey(
        TranslationJob,
        related_name="segments",
        on_delete=models.CASCADE,
    )
    page = models.ForeignKey(
        DocumentPage,
        related_name="translation_segments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    segment_index = models.PositiveIntegerField()
    source_text = models.TextField()
    translated_text = models.TextField(blank=True)
    source_language = models.CharField(max_length=32)
    target_language = models.CharField(max_length=32)
    method = models.CharField(max_length=64, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    bbox = models.JSONField(default=list, blank=True)
    glossary_matches = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["job", "segment_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "segment_index"],
                name="translator_unique_segment_per_job",
            )
        ]
        indexes = [
            models.Index(fields=["job", "needs_review"]),
            models.Index(fields=["source_language", "target_language"]),
        ]

    def __str__(self) -> str:
        return f"{self.job_id} segment {self.segment_index}"


class PhrasebookEntry(TimestampedModel):
    topic = models.CharField(max_length=120, blank=True, db_index=True)
    english = models.TextField(blank=True)
    tagabawa = models.TextField(blank=True)
    filipino = models.TextField(blank=True)
    cebuano = models.TextField(blank=True)
    source = models.CharField(max_length=120, default="phrasebook")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_sme_verified = models.BooleanField(default=False, db_index=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["topic", "english", "id"]
        indexes = [
            models.Index(fields=["is_active", "needs_review"]),
            models.Index(fields=["is_sme_verified", "needs_review"]),
        ]

    def __str__(self) -> str:
        return self.english or self.tagabawa or f"Phrasebook entry {self.pk}"


class GlossaryTerm(TimestampedModel):
    language_pair = models.ForeignKey(
        LanguagePair,
        related_name="glossary_terms",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    source_term = models.CharField(max_length=255)
    target_term = models.CharField(max_length=255)
    domain = models.CharField(max_length=120, blank=True, db_index=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_approved = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_glossary_terms",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["domain", "source_term"]
        constraints = [
            models.UniqueConstraint(
                fields=["language_pair", "source_term", "target_term"],
                name="translator_unique_glossary_term",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source_term} -> {self.target_term}"


class GeneratedOutput(TimestampedModel):
    class OutputType(models.TextChoices):
        TRANSLATED_PDF = "translated_pdf", "Translated PDF"
        BILINGUAL_ALIGNED = "bilingual_aligned", "Aligned bilingual document"
        BILINGUAL_ALTERNATING = "bilingual_alternating", "Alternating bilingual PDF"
        STRUCTURE_JSON = "structure_json", "Structure JSON"
        PREVIEW_IMAGE = "preview_image", "Preview image"

    job = models.ForeignKey(
        TranslationJob,
        related_name="generated_outputs",
        on_delete=models.CASCADE,
    )
    output_type = models.CharField(max_length=32, choices=OutputType.choices)
    file_format = models.CharField(max_length=16, default="pdf")
    file_path = models.TextField()
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["job", "output_type", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "output_type", "file_path"],
                name="translator_unique_generated_output_path",
            )
        ]
        indexes = [
            models.Index(fields=["job", "output_type"]),
            models.Index(fields=["file_format", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.output_type} for {self.job_id}"


class DatasetImportBatch(TimestampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="dataset_imports",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    source_filename = models.CharField(max_length=255)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.QUEUED)
    rows_total = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    error_report = models.JSONField(default=list, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.source_filename} ({self.status})"


class SystemEventLog(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="system_events",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    job = models.ForeignKey(
        TranslationJob,
        related_name="system_events",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    level = models.CharField(max_length=16, choices=Level.choices, default=Level.INFO)
    event_type = models.CharField(max_length=120, db_index=True)
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["level", "-created_at"]),
            models.Index(fields=["event_type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.level}: {self.event_type}"


class UserActivityLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="activity_logs",
        on_delete=models.CASCADE,
    )
    action = models.CharField(max_length=120, db_index=True)
    object_type = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=120, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} {self.action}"
