import zipfile
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from PIL import Image, UnidentifiedImageError


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

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".txt"}
ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf", "application/x-pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    ".jpg": {"image/jpeg", "application/octet-stream"},
    ".jpeg": {"image/jpeg", "application/octet-stream"},
    ".png": {"image/png", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
}


class DocumentUploadForm(forms.Form):
    file = forms.FileField()
    source_language = forms.ChoiceField(choices=LANGUAGE_CHOICES, initial="auto")
    target_language = forms.ChoiceField(choices=TARGET_LANGUAGE_CHOICES, initial="tagabawa")
    ocr_languages = forms.CharField(required=False, max_length=128, widget=forms.HiddenInput())
    ocr_languages_list = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    ocr_languages_custom = forms.CharField(
        required=False,
        max_length=128,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = []
        try:
            from .services.ocr_stage.ocr_service import get_ocr_service
            service = get_ocr_service()
            installed = service.get_installed_languages()
            for lang in installed:
                label = {
                    "eng": "English",
                    "fil": "Filipino",
                    "ceb": "Cebuano",
                    "osd": "OSD",
                }.get(lang, lang.upper())
                choices.append((lang, f"{label} ({lang})"))
        except Exception:
            pass

        fallbacks = [
            ("eng", "English (eng)"),
            ("fil", "Filipino (fil)"),
            ("ceb", "Cebuano (ceb)"),
            ("osd", "Orientation and Script Detection (osd)"),
        ]
        existing_codes = {c[0] for c in choices}
        for code, label in fallbacks:
            if code not in existing_codes:
                choices.append((code, label))

        self.fields["ocr_languages_list"].choices = choices

    def clean(self):
        cleaned_data = super().clean()
        raw_ocr = cleaned_data.get("ocr_languages")
        lst = cleaned_data.get("ocr_languages_list") or []
        custom = cleaned_data.get("ocr_languages_custom") or ""

        has_new_fields = "ocr_languages_list" in self.data or "ocr_languages_custom" in self.data
        if has_new_fields:
            parts = []
            if lst:
                parts.extend(lst)
            if custom.strip():
                custom_parts = [p.strip() for p in custom.replace(",", "+").split("+") if p.strip()]
                for p in custom_parts:
                    if p not in parts:
                        parts.append(p)
            cleaned_data["ocr_languages"] = "+".join(parts)
        else:
            cleaned_data["ocr_languages"] = raw_ocr or ""

        return cleaned_data

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        extension = Path(uploaded.name.lower()).suffix
        if extension not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError("Upload a PDF, DOCX, JPG, PNG, or TXT file.")
        if uploaded.size > settings.LINGOKATUTUBO_MAX_UPLOAD_BYTES:
            max_mb = settings.LINGOKATUTUBO_MAX_UPLOAD_BYTES // (1024 * 1024)
            raise forms.ValidationError(f"File is too large. Maximum size is {max_mb} MB.")
        content_type = (getattr(uploaded, "content_type", "") or "").lower()
        allowed_types = ALLOWED_CONTENT_TYPES.get(extension, set())
        if content_type and content_type not in allowed_types:
            raise forms.ValidationError("The uploaded file type does not match its extension.")
        self._validate_file_signature(uploaded, extension)
        return uploaded

    def _validate_file_signature(self, uploaded, extension: str) -> None:
        try:
            uploaded.seek(0)
            header = uploaded.read(8192)
            uploaded.seek(0)
        except Exception as exc:
            raise forms.ValidationError("Could not inspect the uploaded file.") from exc

        if extension == ".pdf":
            if not header.startswith(b"%PDF-"):
                raise forms.ValidationError("The uploaded PDF is not a valid PDF file.")
            return

        if extension == ".docx":
            if not header.startswith(b"PK"):
                raise forms.ValidationError("The uploaded DOCX is not a valid Office document.")
            try:
                with zipfile.ZipFile(uploaded) as archive:
                    names = set(archive.namelist())
                    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                        raise forms.ValidationError("The uploaded DOCX is missing required document parts.")
            except zipfile.BadZipFile as exc:
                raise forms.ValidationError("The uploaded DOCX is not a valid ZIP package.") from exc
            finally:
                uploaded.seek(0)
            return

        if extension == ".txt":
            try:
                header.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise forms.ValidationError("The uploaded text file must be UTF-8 encoded.") from exc
            return

        try:
            image = Image.open(uploaded)
            image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise forms.ValidationError("The uploaded image is not a valid image file.") from exc
        finally:
            uploaded.seek(0)


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]
