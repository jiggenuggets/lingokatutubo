"""Views for the recognition app: home, registration, photo recognition, history."""
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from ml_model.predictor import predict_image

from .forms import RegisterForm, UploadImageForm
from .models import UploadedImage


def home(request):
    """Public landing page: short description of the system + call-to-action buttons."""
    return render(request, 'recognition/home.html')


def register(request):
    """Create a new account and log the user straight in."""
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            messages.success(request, 'Account created successfully. Welcome!')
            return redirect('home')
    else:
        form = RegisterForm()
    return render(request, 'registration/register.html', {'form': form})


@login_required
def recognize(request):
    """Photo Recognition page: upload an image, run the ML predictor, show the result."""
    if request.method == 'POST':
        form = UploadImageForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = form.save(commit=False)
            uploaded.user = request.user
            uploaded.save()  # save first so uploaded.image.path exists on disk

            prediction = predict_image(uploaded.image.path)
            uploaded.predicted_sign = prediction['predicted_sign']
            uploaded.confidence_score = prediction['confidence_score']
            uploaded.sign_description = prediction['sign_description']
            uploaded.save()

            return redirect('result', pk=uploaded.pk)
    else:
        form = UploadImageForm()
    return render(request, 'recognition/recognize.html', {'form': form})


@login_required
def result(request, pk):
    """Prediction result page for one upload. Only the owner can view it."""
    record = get_object_or_404(UploadedImage, pk=pk, user=request.user)
    return render(request, 'recognition/result.html', {'record': record})


@login_required
def history(request):
    """List the logged-in user's own recognition history."""
    records = UploadedImage.objects.filter(user=request.user)
    return render(request, 'recognition/history.html', {'records': records})


@login_required
def history_delete(request, pk):
    """Confirm-then-delete one history record. Only the owner can delete it."""
    record = get_object_or_404(UploadedImage, pk=pk, user=request.user)
    if request.method == 'POST':
        record.image.delete(save=False)  # also remove the file from media/
        record.delete()
        messages.success(request, 'History record deleted.')
        return redirect('history')
    return render(request, 'recognition/history_confirm_delete.html', {'record': record})
