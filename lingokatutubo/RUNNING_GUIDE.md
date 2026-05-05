# LingoKatutubo – Local Running Guide (Windows)

> **Status:** the backend runs end-to-end for digital PDFs and DOCX. Scanned-PDF/image OCR is **partially implemented** through Tesseract/pytesseract in `backend/ocr_stage/ocr_service.py`. If Tesseract or a needed language pack is unavailable, scanned jobs fail with warnings in `structure.json` instead of silently producing placeholder text.

## Prerequisites

- **Python 3.11** — https://www.python.org/downloads/ (check "Add to PATH")
- **Node.js 18+** — https://nodejs.org/
- **Tesseract OCR** — required for scanned PDF/image OCR. `pytesseract` is installed from `requirements.txt`, but the system Tesseract binary and language packs must also be installed. Set `TESSERACT_CMD` if Tesseract is not on PATH.

---

## Terminal 1 — Backend

```powershell
cd "C:\Users\wapak\Downloads\YAWA\lingokatutubo\backend"

# Create virtual environment (only needed once)
py -3.11 -m venv .venv

# Allow scripts in this PowerShell session
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Start backend server
python -m uvicorn main:app --reload --port 8000
```

Backend URLs:
- API root: http://localhost:8000
- Swagger docs: **http://localhost:8000/docs**
- Health check: http://localhost:8000/health

---

## Terminal 2 — Frontend

```powershell
cd "C:\Users\wapak\Downloads\YAWA\lingokatutubo\frontend"

# Install dependencies (use legacy-peer-deps to avoid peer conflicts)
cmd /c npm install --legacy-peer-deps

# Start frontend dev server
cmd /c npx next dev -p 3000
```

Frontend URL: **http://localhost:3000**

> **Why `cmd /c`?**  PowerShell may block running `npm.cmd`/`.ps1` scripts directly.
> Wrapping with `cmd /c` bypasses that restriction without changing system settings.

---

## Backend API Routes

| Method | URL | Status | Description |
|--------|-----|--------|-------------|
| GET | /health | Implemented | Health check (returns `translation_dataset_loaded`) |
| POST | /translate | Implemented | Upload document, returns `job_id` + `status` |
| GET | /status/{job_id} | Implemented | Poll job status, progress, detected language, confidence |
| GET | /jobs/{job_id} | Implemented | Alias for `/status/{job_id}` |
| GET | /structure/{job_id} | Implemented | Returns structured page/block JSON for the job |
| GET | /preview/{job_id} | Implemented (partly consumed by frontend) | Lists preview image filenames and first-page bilingual blocks |
| GET | /preview-image/{job_id}/{name} | Implemented | Serves a preview PNG |
| GET | /download/{job_id} | Implemented | Returns `translated.pdf` (verifies the file exists first) |
| POST | /quick-translate | Implemented | Translate a single phrase via the dataset cascade |

> The frontend currently consumes `/preview/{job_id}` for the original page preview and `/structure/{job_id}` for first-page translated block data. Full multi-page viewing is still planned.

---

## Supported Languages

| Value | Language | Notes |
|-------|----------|-------|
| `auto` | Auto-Detect | Source only; detects from text |
| `tagabawa` | Bagobo-Tagabawa | Indigenous (dictionary matching) |
| `english` | English | |
| `filipino` | Filipino / Tagalog | |
| `cebuano` | Cebuano | |

---

## Troubleshooting

### PowerShell blocks npm
```powershell
# Option 1: use cmd
cmd /c npm install --legacy-peer-deps
cmd /c npx next dev -p 3000 
# Option 2: allow scripts in this session only
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
npm install --legacy-peer-deps
```

### npm install fails with dependency conflicts
Always add `--legacy-peer-deps`:
```powershell
cmd /c npm install --legacy-peer-deps
```

### "No module named uvicorn"
Make sure the virtual environment is activated before running Python:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Tailwind CSS not loading (plain text / no colours)
Delete the Next.js cache and restart:
```powershell
Remove-Item -Recurse -Force ".next" -ErrorAction SilentlyContinue
cmd /c npx next dev -p 3000 ```

### Frontend can't reach backend
Make sure both terminals are running simultaneously.
The backend (port 8000) must be active before uploading a document.

### pytesseract / Tesseract errors
Scanned PDF/image OCR depends on both the `pytesseract` Python package and a working Tesseract binary. If OCR fails, check that Tesseract is installed, `TESSERACT_CMD` points to `tesseract.exe` when needed, and required language packs are present.

### Dataset shows no translation
The bundled `backend/translation_data.json` uses plain keys such as
`english`, `tagabawa`, `filipino`, and `cebuano`; the loader normalizes
those into the internal `*_source` keys. If translations still return the
original text, check that the phrase exists in the selected source language
and target language. Out-of-vocabulary words are returned as-is.

### Dataset/model expectations
The active dataset has 1015 phrase rows and is used as phrasebook /
translation memory. It is not enough for high-accuracy neural translation.
ByT5-small and NLLB-200 distilled 600M are planned future pretrained-model
paths only; the running backend does not load or invoke either model.

---

## Architecture (current code, not aspirational)

```
lingokatutubo/
├── frontend/                          ← Next.js 15 (React 19, Tailwind v4)
│   ├── app/
│   │   ├── page.tsx                   ← Home page
│   │   ├── translate/page.tsx         ← Upload + status polling + download
│   │   └── about/page.tsx
│   ├── components/navigation.tsx      ← (no side-by-side viewer yet — Planned)
│   ├── hooks/use-upload.ts            ← POST /translate
│   └── lib/utils.ts
└── backend/                           ← FastAPI (Python 3.11)
    ├── main.py                        ← API routes + CORS
    ├── pipeline_service.py            ← Async pipeline orchestration
    ├── detection_service.py           ← Digital vs scanned PDF detection
    ├── extraction_service.py          ← PyMuPDF text/bbox extraction
    ├── reconstruction_service.py      ← Translated PDF + bilingual PDF + previews
    ├── translation_dataset.py         ← exact → fuzzy → word-by-word cascade
    ├── language_detection_service.py  ← langdetect + Tagabawa dictionary
    ├── file_service.py                ← Upload + job dir management
    ├── translation_data.json          ← Phrase dataset
    ├── ocr_stage/                     ← Tesseract-backed OCR service
    └── requirements.txt
```

For full architecture (including aspirational components), see [ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md). Components labelled **Planned** in that file do not exist in code yet.
