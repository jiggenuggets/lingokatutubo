"""Database models for the recognition app."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

# Only these extensions may be uploaded (enforced again, more strictly, in forms.py).
ALLOWED_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png']
MAX_UPLOAD_SIZE_MB = 5


def validate_image_size(uploaded_file):
    """Reject uploaded photos bigger than MAX_UPLOAD_SIZE_MB."""
    max_size_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if uploaded_file.size > max_size_bytes:
        raise ValidationError(f"Image file is too large. Maximum allowed size is {MAX_UPLOAD_SIZE_MB}MB.")


class UploadedImage(models.Model):
    """
    One photo uploaded by a user, together with the FSL sign the model predicted for it.
    A user can only ever see/manage their own rows (enforced in the views, not here).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='uploaded_images',
    )
    image = models.ImageField(
        upload_to='uploads/%Y/%m/%d/',
        validators=[
            FileExtensionValidator(allowed_extensions=ALLOWED_IMAGE_EXTENSIONS),
            validate_image_size,
        ],
        help_text='Accepted formats: JPG, JPEG, PNG. Max size: 5MB.',
    )
    predicted_sign = models.CharField(max_length=50, blank=True)
    confidence_score = models.FloatField(
        default=0.0,
        help_text='Prediction confidence as a percentage (0-100).',
    )
    sign_description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.predicted_sign or 'pending'} ({self.created_at:%Y-%m-%d %H:%M})"
