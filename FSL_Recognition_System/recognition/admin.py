"""Admin panel configuration for the recognition app."""
from django.contrib import admin

from .models import UploadedImage


@admin.register(UploadedImage)
class UploadedImageAdmin(admin.ModelAdmin):
    """Lets admins view every user's uploads/predictions and delete bad data."""

    list_display = ('user', 'predicted_sign', 'confidence_score', 'created_at')
    list_filter = ('predicted_sign', 'created_at')
    search_fields = ('user__username', 'predicted_sign')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)
