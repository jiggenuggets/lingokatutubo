# LingoKatutubo – Local Running Guide (Windows)

> **Status:** the backend runs end-to-end for digital PDFs and DOCX. Scanned-PDF OCR is **Planned (not yet implemented)** — see the README and `pipeline_service.py:_create_mock_layout_for_scanned`. Uploading a scanned PDF will currently produce a placeholder output, not a real translation.

## Prerequisites

- **Python 3.11** — https://www.python.org/downloads/ (check "Add to PATH")
- **Node.js 18+** — https://nodejs.org/
- **Tesseract OCR** — *Not required.* `pytesseract` is listed in `requirements.txt` but **no code imports or calls it today**. Installing Tesseract will not enable scanned-PDF OCR until the OCR branch is wired (see Planned items in `README.md`). The earlier instruction to install Tesseract has been left in `requirements.txt` so the OCR branch can be turned on later without an extra install step.

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
| GET | /preview/{job_id} | Implemented (unused by frontend) | Lists preview image filenames; the current frontend does **not** consume this |
| GET | /preview-image/{job_id}/{name} | Implemented | Serves a preview PNG |
| GET | /download/{job_id} | Implemented | Returns `translated.pdf` (verifies the file exists first) |
| POST | /quick-translate | Implemented | Translate a single phrase via the dataset cascade |

> No structured per-block JSON endpoint exists yet (Planned). The bilingual display work needs one — see the README "Next Development Priorities".

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

### pytesseract errors
*Currently not applicable.* `pytesseract` is declared in `requirements.txt` but no module imports it. If you see a Tesseract error today, the cause is unrelated — check that the package even installed. When OCR is wired (Planned), this section will need to be revisited.

### Dataset shows no translation
The phrase dataset rows need to be added to `backend/translation_data.json`
under a `"rows"` key (see ARCHITECTURE_DIAGRAM.md for the column schema).
The system will still start and the UI will work; untranslatable words are
returned as-is.

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
    ├── ocr_stage/                     ← (empty; reserved for OCR work — Planned)
    └── requirements.txt
```

For full architecture (including aspirational components), see [ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md). Components labelled **Planned** in that file do not exist in code yet.
