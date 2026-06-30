"""Forms for the recognition app: account registration and photo upload."""
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import UploadedImage

# Browsers use this to filter the file picker; the real check happens server-side.
ACCEPTED_CONTENT_TYPES = 'image/png, image/jpeg, image/jpg'


class RegisterForm(UserCreationForm):
    """Sign-up form: Django's built-in username/password fields plus an email."""

    email = forms.EmailField(required=True, help_text='Required.')

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email


class UploadImageForm(forms.ModelForm):
    """Upload form used on the Photo Recognition page. Only asks for the image itself."""

    class Meta:
        model = UploadedImage
        fields = ['image']
        widgets = {
            'image': forms.ClearableFileInput(attrs={
                'accept': ACCEPTED_CONTENT_TYPES,
                'id': 'id_image',
            }),
        }
