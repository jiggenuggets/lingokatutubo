# LingoKatutubo - System Architecture Diagram

> ⚠️ **This diagram is the target architecture.** Several boxes below describe components that are **Planned**, not yet implemented. Read the legend before trusting any box.

## Implementation Legend (verified 2026-05-05)

| Component in diagram | Status |
|---|---|
| Frontend Upload UI | **Implemented** |
| Frontend Progress Modal (multi-step) | **Planned** — current UI is just an inline spinner |
| Frontend Side-by-Side Viewer | **Partially Implemented** - current translate page shows original page preview plus first-page translated blocks; no full `translation-viewer.tsx` |
| Backend `POST /translate`, `GET /status`, `GET /structure`, `GET /preview`, `GET /download`, `GET /preview-image` | **Implemented** |
| Pipeline Phase 1 — Detection | **Implemented** (text-layer probe) |
| Pipeline Phase 2 — Extraction (DIGITAL) | **Partially Implemented** — text + bbox + font name only |
| Pipeline Phase 2 — OCR Service (SCANNED) | **Partially Implemented** - Tesseract/pytesseract is wired for scanned PDFs/images; PaddleOCR is not wired |
| Pipeline Phase 3 — Language Detection (block-level) | **Partially Implemented** — document-level only today |
| Pipeline Phase 4 — Translation cascade (exact → normalized → fuzzy → dictionary → unknown) | **Partially Implemented** — exact + fuzzy + word-by-word; no `[UNKNOWN]` marker, no surfaced confidence |
| Pipeline Phase 4 — Uncertainty Service | **Planned** — no `uncertainty_service.py` |
| Pipeline Phase 5 — Reconstruction (image/line/shape preservation) | **Planned** — current code only re-inserts text with white-box overlay |
| Pipeline Phase 5 — Reconstruction (text reinsertion) | **Implemented** — at line bbox, hardcoded 11pt, default font |
| Pipeline Phase 6 — Preview generation | **Implemented** (first 2 pages) |
| Pipeline Phase 7 — Completion check (output exists) | **Implemented** |
| Job queue (Celery/RQ) | **Planned** — currently `asyncio.create_task` |
| Object storage (S3/GCS) | **Planned** — currently local filesystem under `tempfile.gettempdir()/bagobo-uploads/jobs/` |
| Database for job tracking | **Planned** — currently in-memory dict |
| Pretrained model translation (ByT5 / NLLB) | **Planned** — ByT5-small for Bagobo-Tagabawa directions; NLLB-200 distilled 600M for English/Cebuano/Tagalog; not invoked at runtime; do not train from scratch |

The diagram below shows the **target** architecture. Boxes labelled with services that do not fully exist yet (Uncertainty Service, full Side-by-Side Viewer, Document Translation Progress Modal) are aspirational.

---

## Overall System Flow (target)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           FRONTEND (Next.js + React)                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────┐  │
│  │  Upload UI   │  │   Progress   │  │   Side-by-Side Viewer        │  │
│  │              │  │    Modal     │  │   ┌──────────┬──────────┐    │  │
│  │  • Drag/Drop │  │              │  │   │ Original │Translated│    │  │
│  │  • File      │  │  Queue ─→    │  │   │   PDF    │   PDF    │    │  │
│  │    Select    │  │  Parse ─→    │  │   │          │          │    │  │
│  │  • Language  │  │  OCR   ─→    │  │   │  Page 1  │  Page 1  │    │  │
│  │    Select    │  │  Trans.─→    │  │   │          │          │    │  │
│  │              │  │  Layout      │  │   └──────────┴──────────┘    │  │
│  └──────┬───────┘  └──────┬───────┘  │   [Toolbar: Pages|Zoom|Download]│
│         │                 │           └────────────────┬─────────────┘  │
│         │                 │                            │                 │
└─────────┼─────────────────┼────────────────────────────┼─────────────────┘
          │                 │                            │
          │ POST /translate │ GET /status/{id}           │ GET /preview/{id}
          ▼                 ▼                            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        BACKEND API (FastAPI)                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  Endpoints:                                                              │
│  • POST /translate       ─→  Upload & Start Job                         │
│  • GET  /status/{id}     ─→  Poll Job Progress                          │
│  • GET  /preview/{id}    ─→  Get Preview Images                         │
│  • GET  /download/{id}   ─→  Download Translated PDF                    │
│  • GET  /preview-image/{id}/{name} ─→ Serve Preview PNG                 │
│                                                                           │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │
                            │ Async Job Processing
                            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       PIPELINE SERVICE                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Main Orchestrator: process_translation()                               │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  Phase 1: Detection (10%)                                   │        │
│  │  ┌────────────────────────────────────────────────┐         │        │
│  │  │ Detection Service                              │         │        │
│  │  │ • detect_pdf_type() ─→ DIGITAL or SCANNED      │         │        │
│  │  └────────────────────────────────────────────────┘         │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  Phase 2: Extraction (25%)                                  │        │
│  │  ┌────────────────────────────────────────────────┐         │        │
│  │  │ Extraction Service (DIGITAL)                   │         │        │
│  │  │ • extract_pdf_with_layout_preservation()       │         │        │
│  │  │   Returns:                                     │         │        │
│  │  │   - Text blocks with coordinates               │         │        │
│  │  │   - Non-text objects (images, lines, shapes)   │         │        │
│  │  │   - Font info, colors, alignment               │         │        │
│  │  └────────────────────────────────────────────────┘         │        │
│  │                         OR                                  │        │
│  │  ┌────────────────────────────────────────────────┐         │        │
│  │  │ OCR Service (SCANNED)                          │         │        │
│  │  │ • process_scanned_pdf()                        │         │        │
│  │  │   - Convert pages to images                    │         │        │
│  │  │   - Run Tesseract OCR                          │         │        │
│  │  │   - Extract text with bounding boxes           │         │        │
│  │  └────────────────────────────────────────────────┘         │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  Phase 3: Language Detection (40%)                          │        │
│  │  ┌────────────────────────────────────────────────┐         │        │
│  │  │ Language Detection Service                     │         │        │
│  │  │ • detect_block_language(text)                  │         │        │
│  │  │   - English, Filipino, Cebuano, Tagabawa       │         │        │
│  │  │   - Confidence scores                          │         │        │
│  │  │   - Handle mixed language                      │         │        │
│  │  └────────────────────────────────────────────────┘         │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  Phase 4: Translation (50-65%)                              │        │
│  │  ┌────────────────────────────────────────────────┐         │        │
│  │  │ Translation Dataset Service                    │         │        │
│  │  │ Strategy Cascade:                              │         │        │
│  │  │                                                │         │        │
│  │  │ 1. Exact Match      ─→ confidence: 1.0         │         │        │
│  │  │    phrase_index lookup                         │         │        │
│  │  │                                                │         │        │
│  │  │ 2. Normalized Match ─→ confidence: 0.95        │         │        │
│  │  │    lowercase, strip punctuation                │         │        │
│  │  │                                                │         │        │
│  │  │ 3. Fuzzy Match      ─→ confidence: 0.7-0.9     │         │        │
│  │  │    fuzzywuzzy similarity > 85%                 │         │        │
│  │  │                                                │         │        │
│  │  │ 4. Dictionary       ─→ confidence: 0.6         │         │        │
│  │  │    DuBois dictionary word lookup               │         │        │
│  │  │                                                │         │        │
│  │  │ 5. Unknown Flag     ─→ confidence: 0.0         │         │        │
│  │  │    [UNKNOWN: text]                             │         │        │
│  │  └────────────────────────────────────────────────┘         │        │
│  │                         +                                   │        │
│  │  ┌────────────────────────────────────────────────┐         │        │
│  │  │ Uncertainty Service                            │         │        │
│  │  │ • Flag low confidence (<0.7)                   │         │        │
│  │  │ • Mark special terms                           │         │        │
│  │  │ • Generate suggestions                         │         │        │
│  │  └────────────────────────────────────────────────┘         │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  Phase 5: PDF Reconstruction (75-85%)                       │        │
│  │  ┌────────────────────────────────────────────────┐         │        │
│  │  │ Reconstruction Service                         │         │        │
│  │  │ • reconstruct_pdf_with_layout()                │         │        │
│  │  │                                                │         │        │
│  │  │   Step 1: Copy Non-Text Objects                │         │        │
│  │  │   ┌────────────────────────────┐               │         │        │
│  │  │   │ • Images  ─→ Same position │               │         │        │
│  │  │   │ • Logos   ─→ Same position │               │         │        │
│  │  │   │ • Lines   ─→ Same position │               │         │        │
│  │  │   │ • Shapes  ─→ Same position │               │         │        │
│  │  │   └────────────────────────────┘               │         │        │
│  │  │                                                │         │        │
│  │  │   Step 2: Insert Translated Text               │         │        │
│  │  │   ┌────────────────────────────┐               │         │        │
│  │  │   │ • Use original coordinates │               │         │        │
│  │  │   │ • Preserve font family     │               │         │        │
│  │  │   │ • Adjust size if needed    │               │         │        │
│  │  │   │ • Keep color & alignment   │               │         │        │
│  │  │   │ • Handle text wrapping     │               │         │        │
│  │  │   └────────────────────────────┘               │         │        │
│  │  └────────────────────────────────────────────────┘         │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  Phase 6: Preview Generation (85-95%)                       │        │
│  │  ┌────────────────────────────────────────────────┐         │        │
│  │  │ Reconstruction Service                         │         │        │
│  │  │ • create_preview_images()                      │         │        │
│  │  │   - Convert PDF pages to PNG                   │         │        │
│  │  │   - Generate for original PDF                  │         │        │
│  │  │   - Generate for translated PDF                │         │        │
│  │  │   - Store in job preview directory             │         │        │
│  │  └────────────────────────────────────────────────┘         │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  Phase 7: Completion (100%)                                 │        │
│  │  • Set job status = "completed"                             │        │
│  │  • Store output paths                                       │        │
│  │  • Return success                                           │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘


## Data Flow: Layout Preservation

┌─────────────────────────────────────────────────────────────────────────┐
│                          INPUT PDF                                      │
│  ┌─────────────────────────────────────────────────────────┐            │
│  │                                                         │            │
│  │  [LOGO]                                                 │            │
│  │  ══════════════════════════════════════════             │            │
│  │                                                         │            │
│  │  Heading Text Here                                      │            │
│  │                                                         │            │
│  │  This is a paragraph with some text content.            │            │
│  │  It has multiple lines and maintains formatting.        │            │
│  │                                                         │            │
│  │  • Bullet point one                                     │            │
│  │  • Bullet point two                                     │            │
│  │                                                         │            │
│  │        [IMAGE]                                          │            │
│  │                                                         │            │
│  └─────────────────────────────────────────────────────────┘            │
└────────────────────────┬────────────────────────────────────────────────┘
                         │
                         │ EXTRACTION
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STRUCTURED PAGE DATA                                 │
├─────────────────────────────────────────────────────────────────────────┤
│  Page 0 (width: 612, height: 792)                                       │
│                                                                         │
│  NON-TEXT OBJECTS (copied as-is):                                       │
│  • image(bbox: [100, 50, 200, 100])    ← Logo                           │
│  • line(bbox: [50, 110, 562, 112])     ← Horizontal line                │
│  • image(bbox: [200, 400, 400, 500])   ← Image                          │
│                                                                         │
│  TEXT BLOCKS (translated):                                              │
│  • block_1(bbox: [50, 130, 300, 150])                                   │
│    text: "Heading Text Here"                                            │
│    font: Arial-Bold, size: 16, color: (0,0,0)                           │
│                                                                         │
│  • block_2(bbox: [50, 170, 562, 210])                                   │
│    text: "This is a paragraph with some text content..."                │
│    font: Arial, size: 11, color: (0,0,0)                                │
│                                                                         │
│  • block_3(bbox: [70, 230, 562, 250])                                   │
│    text: "• Bullet point one"                                           │
│    font: Arial, size: 11, color: (0,0,0)                                │
│                                                                         │
│  • block_4(bbox: [70, 250, 562, 270])                                   │
│    text: "• Bullet point two"                                           │
│    font: Arial, size: 11, color: (0,0,0)                                │
└─────────────────────────────────────────────────────────────────────────┘
                         │
                         │ TRANSLATION
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       TRANSLATION RESULTS                               │
├─────────────────────────────────────────────────────────────────────────┤
│  "Heading Text Here" ──→ "Pamagat ng Teksto Dito"                       │
│    method: exact, confidence: 1.0                                       │
│                                                                         │
│  "This is a paragraph..." ──→ "Ito ay isang talata..."                  │
│    method: fuzzy, confidence: 0.85                                      │
│                                                                         │
│  "Bullet point one" ──→ "Unang bullet point"                            │
│    method: normalized, confidence: 0.95                                 │
│                                                                         │
│  "Bullet point two" ──→ "Ikalawang bullet point"                        │
│    method: normalized, confidence: 0.95                                 │
└─────────────────────────────────────────────────────────────────────────┘
                         │
                         │ RECONSTRUCTION
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        OUTPUT PDF                                       │
│  ┌─────────────────────────────────────────────────────────┐            │
│  │                                                         │            │
│  │  [LOGO] ← Same position (100, 50, 200, 100)             │            │
│  │  ══════════════════════════════════════════             │            │
│  │         ← Same position (50, 110, 562, 112)             │            │
│  │  Pamagat ng Teksto Dito ← Translated text               │            │
│  │                           Same bbox (50, 130, 300, 150) │            │
│  │  Ito ay isang talata... ← Translated text               │            │
│  │                          Same bbox (50, 170, 562, 210)  │            │
│  │                                                         │            │
│  │  • Unang bullet point ← Translated                      │            │
│  │  • Ikalawang bullet point                               │            │
│  │                                                         │            │
│  │        [IMAGE] ← Same position (200, 400, 400, 500)     │            │
│  │                                                         │            │
│  └─────────────────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────────────┘


## Translation Strategy Decision Tree

                    Input: Text Block
                           │
                           ▼
           ┌───────────────────────────────┐
           │  Try Exact Phrase Match       │
           │  phrase_index[text]           │
           └───────────┬───────────────────┘
                       │
                 Found?│       Not Found
                  Yes  │          │
                       ▼          ▼
                ┌──────────┐  ┌──────────────────────┐
                │ Return   │  │ Try Normalized Match │
                │ (conf:   │  │ lowercase, no punct. │
                │  1.0)    │  └──────┬───────────────┘
                └──────────┘         │
                                Found?│       Not Found
                                 Yes  │          │
                                      ▼          ▼
                               ┌──────────┐  ┌──────────────────┐
                               │ Return   │  │ Try Fuzzy Match  │
                               │ (conf:   │  │ similarity > 85% │
                               │  0.95)   │  └──────┬───────────┘
                               └──────────┘         │
                                               Found?│       Not Found
                                                Yes  │          │
                                                     ▼          ▼
                                              ┌──────────┐  ┌────────────────┐
                                              │ Return   │  │ Try Dictionary │
                                              │ (conf:   │  │ word-by-word   │
                                              │ 0.7-0.9) │  └──────┬─────────┘
                                              └──────────┘         │
                                                              Found?│   Not Found
                                                               Yes  │      │
                                                                    ▼      ▼
                                                             ┌──────────┐  ┌────────────┐
                                                             │ Return   │  │ Flag as    │
                                                             │ (conf:   │  │ [UNKNOWN]  │
                                                             │  0.6)    │  │ (conf: 0.0)│
                                                             └──────────┘  └────────────┘


## File Storage Structure

jobs/
  {job_id}/
    ├── input/
    │   └── original.pdf              ← Uploaded file
    ├── structured/
    │   └── layout_data.json          ← Extracted structure
    ├── translated/
    │   └── translations.json         ← Translation results
    ├── output/
    │   ├── translated.pdf            ← Final translated PDF
    │   └── bilingual.pdf             ← Side-by-side version
    └── preview/
        ├── original_page_0.png       ← Preview: Original
        ├── original_page_1.png
        ├── translated_page_0.png     ← Preview: Translated
        └── translated_page_1.png


## Technology Stack

Frontend (current):
  • Next.js 15 + React 19
  • TypeScript
  • Tailwind CSS v4
  • Lucide React (icons)
  • SWR (installed; not currently used in the translate flow)

Backend (current):
  • FastAPI (Python 3.11)
  • PyMuPDF (fitz) — PDF read/extract/reconstruct/preview
  • python-docx — DOCX paragraph extraction
  • Pillow — image handling
  • ReportLab — installed; not actively used (PyMuPDF handles PDF output)
  • rapidfuzz — fuzzy matching in the translation cascade
  • langdetect — non-Tagabawa language detection
  • openpyxl — optional dataset loader path
  • pytesseract — used by `backend/ocr_stage/ocr_service.py` for scanned PDFs/images

Backend (planned but not installed in the active backend venv):
  • PaddleOCR — not wired in active backend code
  • transformers + sentencepiece + torch — for planned ByT5-small / NLLB-200 distilled fine-tuning/inference

Dataset/model prep:
  • `backend/translation_data.json` has 1015 phrase rows and is used as phrasebook / translation memory
  • recommended future training data format is directional JSONL pairs
  • ByT5-small is planned for Bagobo-Tagabawa directions
  • NLLB-200 distilled 600M is planned for English-Cebuano-Tagalog directions
  • do not train from scratch

Storage:
  • Local filesystem (development)
  • S3/GCS (production)

Database:
  • In-memory dict (development)
  • PostgreSQL/MongoDB (production)

Job Queue:
  • asyncio (development)
  • Celery/RQ (production)


## Key Goal: Layout-Aware Reconstruction

Traditional translation rewrites the entire document. LingoKatutubo *attempts* to preserve the original visual structure:

Target approach:
  1. Separate text from non-text objects
  2. Keep non-text objects at original positions
  3. Translate only text
  4. Place translated text into the original bboxes

> **Honest claim today:** the system performs **partial layout preservation** for digital PDFs. It re-inserts translated text at the original line bbox, but does **not** yet preserve images, lines, or shapes through reconstruction (the white overlay can erase them), and does not adapt font/size/color/alignment. Do not describe the output as a "perfect visual match" or "exact replica" in any defense or report — see [claude.md](claude.md) "Layout preservation truthfulness" and [agent.md](agent.md) "Honest current claim".

This still matters for educational materials because:
  • Diagrams should align with text
  • Cultural symbols should stay intact
  • Page structure aids learning
  • Teacher annotations remain valid

— but those goals are work-in-progress, not delivered guarantees.
