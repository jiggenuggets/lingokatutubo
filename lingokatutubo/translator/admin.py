from django.contrib import admin

from .models import (
    DatasetImportBatch,
    DocumentPage,
    GeneratedOutput,
    GlossaryTerm,
    Language,
    LanguagePair,
    OCRResult,
    PhrasebookEntry,
    SystemEventLog,
    TranslationJob,
    TranslationSegment,
    UploadedDocument,
    UserActivityLog,
)


class DocumentPageInline(admin.TabularInline):
    model = DocumentPage
    extra = 0
    fields = ("page_number", "width", "height", "rotation", "source_image_path")
    readonly_fields = ("page_number", "width", "height", "rotation", "source_image_path")
    can_delete = False
    show_change_link = True


class TranslationSegmentInline(admin.TabularInline):
    model = TranslationSegment
    extra = 0
    fields = (
        "segment_index",
        "source_language",
        "target_language",
        "method",
        "confidence",
        "needs_review",
    )
    readonly_fields = fields
    can_delete = False
    show_change_link = True


class GeneratedOutputInline(admin.TabularInline):
    model = GeneratedOutput
    extra = 0
    fields = ("output_type", "file_format", "file_path", "file_size_bytes", "created_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(TranslationJob)
class TranslationJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "original_filename",
        "owner",
        "status",
        "progress",
        "extraction_method_display",
        "ocr_confidence_display",
        "source_language",
        "target_language",
        "is_deleted",
        "created_at",
    )
    list_filter = ("status", "file_type", "source_language", "target_language", "is_deleted", "created_at")
    search_fields = ("id", "original_filename", "owner__username")
    readonly_fields = (
        "id", "created_at", "updated_at", "completed_at", "deleted_at",
        "extraction_method_display", "ocr_confidence_display",
    )
    inlines = (DocumentPageInline, TranslationSegmentInline, GeneratedOutputInline)

    @admin.display(description="Extraction Method")
    def extraction_method_display(self, obj):
        method = (obj.metadata or {}).get("extraction_method", "")
        labels = {
            "direct_pdf_text": "Direct PDF Text",
            "ocr_image": "OCR (Tesseract)",
            "docx_text": "DOCX Text",
            "plain_text": "Plain Text",
            "hybrid": "Hybrid",
        }
        return labels.get(method, method or "—")

    @admin.display(description="OCR Confidence")
    def ocr_confidence_display(self, obj):
        summary = (obj.metadata or {}).get("ocr_summary") or {}
        mean = summary.get("mean_confidence")
        if mean is None:
            return "N/A"
        pct = round(mean * 100)
        warning = " ⚠" if summary.get("has_low_quality_warning") else ""
        return f"{pct}%{warning}"


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "autonym", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "autonym")


@admin.register(LanguagePair)
class LanguagePairAdmin(admin.ModelAdmin):
    list_display = ("source_language", "target_language", "is_active", "updated_at")
    list_filter = ("is_active", "source_language", "target_language")
    search_fields = ("source_language__code", "target_language__code")


@admin.register(UploadedDocument)
class UploadedDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "owner",
        "file_type",
        "file_size_bytes",
        "created_at",
    )
    list_filter = ("file_type", "created_at")
    search_fields = ("original_filename", "owner__username", "checksum_sha256")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DocumentPage)
class DocumentPageAdmin(admin.ModelAdmin):
    list_display = ("job", "page_number", "width", "height", "rotation", "updated_at")
    list_filter = ("rotation", "created_at")
    search_fields = ("job__id", "job__original_filename", "extracted_text")
    readonly_fields = ("created_at", "updated_at")


@admin.register(OCRResult)
class OCRResultAdmin(admin.ModelAdmin):
    list_display = (
        "job", "page", "engine", "language_codes",
        "confidence_display", "warnings_count", "status", "created_at",
    )
    list_filter = ("status", "engine", "created_at")
    search_fields = ("job__id", "job__original_filename", "text", "engine")
    readonly_fields = ("created_at", "updated_at", "confidence_display", "warnings_count")
    actions = ("mark_accepted", "mark_pending_review")

    @admin.display(description="Confidence")
    def confidence_display(self, obj):
        if obj.confidence is None:
            return "—"
        pct = round(obj.confidence * 100)
        return f"{pct}%"

    @admin.display(description="Warnings")
    def warnings_count(self, obj):
        warnings = obj.warnings or []
        return len(warnings)

    @admin.action(description="Mark selected OCR results as accepted")
    def mark_accepted(self, request, queryset):
        queryset.update(status=OCRResult.Status.ACCEPTED)

    @admin.action(description="Mark selected OCR results as pending review")
    def mark_pending_review(self, request, queryset):
        queryset.update(status=OCRResult.Status.PENDING_REVIEW)


@admin.register(TranslationSegment)
class TranslationSegmentAdmin(admin.ModelAdmin):
    list_display = (
        "job",
        "segment_index",
        "source_language",
        "target_language",
        "method",
        "confidence",
        "needs_review",
    )
    list_filter = ("needs_review", "source_language", "target_language", "method", "created_at")
    search_fields = ("job__id", "source_text", "translated_text")
    readonly_fields = ("created_at", "updated_at")
    actions = ("mark_needs_review", "mark_reviewed")

    @admin.action(description="Mark selected segments as needing review")
    def mark_needs_review(self, request, queryset):
        queryset.update(needs_review=True)

    @admin.action(description="Mark selected segments as reviewed")
    def mark_reviewed(self, request, queryset):
        queryset.update(needs_review=False)


@admin.register(PhrasebookEntry)
class PhrasebookEntryAdmin(admin.ModelAdmin):
    list_display = (
        "topic",
        "english",
        "tagabawa",
        "filipino",
        "cebuano",
        "is_sme_verified",
        "needs_review",
        "is_active",
    )
    list_filter = ("is_active", "is_sme_verified", "needs_review", "topic", "source")
    search_fields = ("english", "tagabawa", "filipino", "cebuano", "notes")
    readonly_fields = ("created_at", "updated_at")
    actions = ("mark_verified", "mark_needs_review")

    @admin.action(description="Mark selected phrasebook entries as SME verified")
    def mark_verified(self, request, queryset):
        queryset.update(is_sme_verified=True, needs_review=False)

    @admin.action(description="Mark selected phrasebook entries as needing review")
    def mark_needs_review(self, request, queryset):
        queryset.update(needs_review=True)


@admin.register(GlossaryTerm)
class GlossaryTermAdmin(admin.ModelAdmin):
    list_display = (
        "source_term",
        "target_term",
        "language_pair",
        "domain",
        "is_approved",
        "is_active",
    )
    list_filter = ("is_active", "is_approved", "domain", "language_pair")
    search_fields = ("source_term", "target_term", "notes")
    readonly_fields = ("created_at", "updated_at")
    actions = ("approve_terms",)

    @admin.action(description="Approve selected glossary terms")
    def approve_terms(self, request, queryset):
        queryset.update(is_approved=True)


@admin.register(GeneratedOutput)
class GeneratedOutputAdmin(admin.ModelAdmin):
    list_display = ("job", "output_type", "file_format", "file_size_bytes", "created_at")
    list_filter = ("output_type", "file_format", "created_at")
    search_fields = ("job__id", "file_path")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DatasetImportBatch)
class DatasetImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "source_filename",
        "uploaded_by",
        "status",
        "rows_total",
        "rows_created",
        "rows_updated",
        "rows_failed",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("source_filename", "uploaded_by__username")
    readonly_fields = ("created_at", "updated_at", "completed_at")


@admin.register(SystemEventLog)
class SystemEventLogAdmin(admin.ModelAdmin):
    list_display = ("level", "event_type", "actor", "job", "created_at")
    list_filter = ("level", "event_type", "created_at")
    search_fields = ("message", "actor__username", "job__id")
    readonly_fields = ("created_at",)

    def has_add_permission(self, request):
        return False


@admin.register(UserActivityLog)
class UserActivityLogAdmin(admin.ModelAdmin):
    list_display = ("user", "action", "object_type", "object_id", "created_at")
    list_filter = ("action", "object_type", "created_at")
    search_fields = ("user__username", "action", "object_id")
    readonly_fields = ("created_at",)

    def has_add_permission(self, request):
        return False
