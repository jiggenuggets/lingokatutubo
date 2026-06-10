from django.contrib.auth import views as auth_views
from django.urls import path

from . import views


app_name = "translator"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("accounts/signup/", views.signup, name="signup"),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("translate/", views.translate, name="translate"),
    path("translate/preview/<uuid:job_id>/", views.preview, name="preview"),
    path("translate/upload/", views.api_translate, name="upload"),
    path("translate/status/<uuid:job_id>/", views.api_job_status, name="status"),
    path("translate/structure/<uuid:job_id>/", views.api_structure, name="structure"),
    path("translate/preview-data/<uuid:job_id>/", views.api_preview, name="preview_data"),
    path(
        "translate/preview-image/<uuid:job_id>/<str:image_name>/",
        views.preview_image,
        name="translate_preview_image",
    ),
    path("translate/download/<uuid:job_id>/", views.download_job, name="translate_download"),
    path("health/", views.health, name="health"),

    # Compatibility aliases for earlier Next.js/FastAPI-style API calls.
    path("api/translate/", views.api_translate, name="api_translate"),
    path("api/jobs/<uuid:job_id>/", views.api_job_status, name="api_job_status"),
    path("api/status/<uuid:job_id>/", views.api_job_status, name="api_status"),
    path("api/structure/<uuid:job_id>/", views.api_structure, name="api_structure"),
    path("api/preview/<uuid:job_id>/", views.api_preview, name="api_preview"),
    path(
        "api/preview-image/<uuid:job_id>/<str:image_name>/",
        views.preview_image,
        name="preview_image",
    ),
    path("api/download/<uuid:job_id>/", views.download_job, name="download"),
    path("api/quick-translate/", views.quick_translate, name="quick_translate"),
]
