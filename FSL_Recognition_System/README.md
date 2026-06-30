# Filipino Sign Language Photo Recognition System

A Django web app that recognizes Filipino Sign Language (FSL) from
**uploaded photos** (not live video). A logged-in user uploads a hand sign
photo, and the system predicts the most likely FSL letter, shows a
confidence score and a short explanation, and saves the result to that
user's recognition history.

## Tech stack

- Backend: Django 6
- Frontend: plain HTML, CSS, and JavaScript (no React/Next.js/mobile framework)
- ML: TensorFlow/Keras integration point in [ml_model/](ml_model/) (placeholder
  predictor until a trained model is added — see [ml_model/README.md](ml_model/README.md))

## Project structure

```
FSL_Recognition_System/
├── manage.py
├── requirements.txt
├── sign_language_project/   # Django project (settings, urls)
├── recognition/              # Main app: models, views, forms, admin, urls
├── templates/                 # HTML templates (base, registration/, recognition/)
├── static/                    # CSS and JavaScript
│   ├── css/style.css
│   └── js/preview.js
├── media/                     # User-uploaded photos (created at runtime)
├── ml_model/                  # ML integration point (predictor.py)
└── dataset/                   # Training images, organized A-Z
```

## Setup instructions

From the `FSL_Recognition_System` folder:

**1. Create a virtual environment**

```bash
python -m venv .venv
```

**2. Activate it**

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (cmd)
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate
```

**3. Install requirements**

```bash
pip install -r requirements.txt
```

**4. Run migrations**

```bash
python manage.py migrate
```

**5. Create a superuser (for the admin panel)**

```bash
python manage.py createsuperuser
```

**6. Run the development server**

```bash
python manage.py runserver
```

Then open http://127.0.0.1:8000/ in your browser. The admin panel is at
http://127.0.0.1:8000/admin/.

## Using the system

1. Register an account (or log in if you already have one).
2. Click **Start Recognition**, choose a JPG/JPEG/PNG photo of a hand sign,
   preview it, and click **Recognize Sign**.
3. View the predicted sign, confidence score, and explanation on the result page.
4. Visit **History** to see all of your past uploads, view a result again, or delete it.

## Connecting a real trained model

The system currently uses a **placeholder predictor** (random label +
confidence) so the whole app works end-to-end without a trained model. To
plug in a real one, see [ml_model/README.md](ml_model/README.md) — in short,
save your trained Keras model as `ml_model/fsl_model.h5` and it will be
picked up automatically; no other code changes are required.

## Notes on validation & security

- Uploaded files are restricted to `.jpg`, `.jpeg`, `.png` and 5MB max, checked
  both in the browser (for fast feedback) and on the server (authoritative).
- Django's `ImageField` verifies the upload is a real, openable image.
- All upload/history pages require login (`@login_required`).
- A user can only view or delete their **own** history records.
- CSRF protection is on by default for all forms.
