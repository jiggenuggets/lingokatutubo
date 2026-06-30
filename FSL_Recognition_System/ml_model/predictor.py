"""
Filipino Sign Language (FSL) image classification.

This module is the single integration point between the Django app and the
ML model. It currently has no trained weights, so predict_image() falls back
to a placeholder predictor — this lets the rest of the system (upload form,
history, admin, etc.) be built and tested end-to-end before a real model
exists.

To plug in a real trained model later:
    1. Export it as a Keras model and save it as ml_model/fsl_model.h5
       (or change MODEL_PATH below to match your file).
    2. Make sure CLASS_LABELS below matches the class order the model was
       trained with (the dataset/ folder, A-Z, was used as a starting point).
    3. Update _model_predict()'s preprocessing (IMAGE_SIZE, normalization)
       to match how the model was trained.
No other file needs to change — predict_image() will automatically pick up
the real model instead of the placeholder as soon as MODEL_PATH exists.
"""
import random
from pathlib import Path

from PIL import Image

ML_MODEL_DIR = Path(__file__).resolve().parent
MODEL_PATH = ML_MODEL_DIR / 'fsl_model.h5'

# Filipino Sign Language manual alphabet, A-Z (matches the dataset/ folder names).
CLASS_LABELS = [chr(code) for code in range(ord('A'), ord('Z') + 1)]

# Short, beginner-friendly explanation shown next to each prediction.
SIGN_DESCRIPTIONS = {
    letter: f"This is the fingerspelled letter ‘{letter}’ from the Filipino Sign Language (FSL) manual alphabet."
    for letter in CLASS_LABELS
}

_model = None  # cached model instance, loaded lazily on first real prediction


def _try_load_model():
    """Load the trained Keras model once and cache it. Returns None if not available yet."""
    global _model
    if _model is not None:
        return _model
    if not MODEL_PATH.exists():
        return None
    try:
        from tensorflow import keras  # imported lazily so the app still runs without a model
        _model = keras.models.load_model(MODEL_PATH)
    except Exception:
        _model = None
    return _model


def _placeholder_predict(image_path):
    """
    Stand-in prediction used until a real trained model is dropped into ml_model/.
    Returns a random label + confidence so the UI/history/admin can be exercised.
    """
    label = random.choice(CLASS_LABELS)
    confidence = round(random.uniform(60.0, 99.0), 2)
    return label, confidence


def _model_predict(model, image_path):
    """Run the real trained model on the uploaded image."""
    import numpy as np

    image_size = (224, 224)
    img = Image.open(image_path).convert('RGB').resize(image_size)
    array = np.asarray(img, dtype='float32') / 255.0
    array = np.expand_dims(array, axis=0)

    predictions = model.predict(array, verbose=0)[0]
    best_index = int(np.argmax(predictions))
    label = CLASS_LABELS[best_index] if best_index < len(CLASS_LABELS) else 'Unknown'
    confidence = round(float(predictions[best_index]) * 100, 2)
    return label, confidence


def predict_image(image_path):
    """
    Predict the FSL sign shown in the image at image_path.

    Returns a dict: {"predicted_sign", "confidence_score", "sign_description"}.
    Uses the trained model in ml_model/ when present, otherwise the placeholder.
    """
    model = _try_load_model()
    if model is not None:
        label, confidence = _model_predict(model, image_path)
    else:
        label, confidence = _placeholder_predict(image_path)

    description = SIGN_DESCRIPTIONS.get(label, 'No description available for this sign yet.')
    return {
        'predicted_sign': label,
        'confidence_score': confidence,
        'sign_description': description,
    }
