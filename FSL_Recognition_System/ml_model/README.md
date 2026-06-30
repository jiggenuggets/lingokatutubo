# ml_model

This folder is where the trained Filipino Sign Language (FSL) image
classification model lives.

- `predictor.py` — loads the model and exposes `predict_image(image_path)`,
  the only function the Django views call.
- No trained model yet? `predictor.py` automatically falls back to a
  placeholder predictor (random label + confidence) so the rest of the app
  keeps working.

## Adding a real trained model

1. Train a Keras model that classifies the `dataset/A` ... `dataset/Z`
   folders (or your own classes).
2. Save it as `ml_model/fsl_model.h5`.
3. If your class order differs from A-Z, update `CLASS_LABELS` in
   `predictor.py`.
4. If your training preprocessing differs (image size, normalization),
   update `_model_predict()` in `predictor.py` to match.

That's it — `predict_image()` will use the real model automatically as soon
as `fsl_model.h5` exists, no other code changes needed.
