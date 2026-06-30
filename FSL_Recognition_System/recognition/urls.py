"""URL routes for the recognition app."""
from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('register/', views.register, name='register'),
    path('recognize/', views.recognize, name='recognize'),
    path('result/<int:pk>/', views.result, name='result'),
    path('history/', views.history, name='history'),
    path('history/<int:pk>/delete/', views.history_delete, name='history_delete'),
]
