"""Root URL configuration for LingoKatutubo."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, reverse_lazy


urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="translator/password_reset_form.html",
            email_template_name="translator/password_reset_email.html",
            subject_template_name="translator/password_reset_subject.txt",
            success_url=reverse_lazy("password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "accounts/password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="translator/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="translator/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="translator/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    path("", include("translator.urls")),
]
