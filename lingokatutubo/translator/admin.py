from django.contrib import admin

from .models import TranslationJob


@admin.register(TranslationJob)
class TranslationJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "original_filename",
        "owner",
        "status",
        "progress",
        "source_language",
        "target_language",
        "created_at",
    )
    list_filter = ("status", "file_type", "source_language", "target_language", "created_at")
    search_fields = ("id", "original_filename", "owner__username")
    readonly_fields = ("id", "created_at", "updated_at", "completed_at")
