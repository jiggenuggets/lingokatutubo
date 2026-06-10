from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


LANGUAGE_CHOICES = [
    ("auto", "Auto-Detect"),
    ("english", "English"),
    ("filipino", "Filipino / Tagalog"),
    ("cebuano", "Cebuano"),
    ("tagabawa", "Bagobo-Tagabawa"),
]

TARGET_LANGUAGE_CHOICES = [
    ("tagabawa", "Bagobo-Tagabawa"),
    ("english", "English"),
    ("filipino", "Filipino / Tagalog"),
    ("cebuano", "Cebuano"),
]

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}


class DocumentUploadForm(forms.Form):
    file = forms.FileField()
    source_language = forms.ChoiceField(choices=LANGUAGE_CHOICES, initial="auto")
    target_language = forms.ChoiceField(choices=TARGET_LANGUAGE_CHOICES, initial="tagabawa")
    ocr_languages = forms.CharField(required=False, max_length=128)

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        name = uploaded.name.lower()
        if not any(name.endswith(extension) for extension in ALLOWED_EXTENSIONS):
            raise forms.ValidationError("Upload a PDF, DOCX, JPG, or PNG file.")
        if uploaded.size > settings.LINGOKATUTUBO_MAX_UPLOAD_BYTES:
            max_mb = settings.LINGOKATUTUBO_MAX_UPLOAD_BYTES // (1024 * 1024)
            raise forms.ValidationError(f"File is too large. Maximum size is {max_mb} MB.")
        return uploaded


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]
