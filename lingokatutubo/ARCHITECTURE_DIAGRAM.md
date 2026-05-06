# LingoKatutubo - System Architecture Diagram

> ⚠️ **This diagram is the target architecture.** Several boxes below describe components that are **Planned**, not yet implemented. Read the legend before trusting any box.

## Implementation Legend (verified 2026-05-07)

| Component in diagram | Status |
|---|---|
| Frontend Upload UI | **Implemented** |
| Frontend Progress UI | **Implemented** — inline progress card with phase, percent, current step, message; backed by `PIPELINE_PHASES` (no separate modal component, by design) |
| Frontend Side-by-Side Viewer | **Implemented** — dedicated route at `/translate/preview/[jobId]` with Back, Download, Original/Translated images, Page X of N, Previous/Next, and a collapsible Translation Details section |
| Backend `POST /translate`, `GET /status`, `GET /structure`, `GET /preview`, `GET /download`, `GET /preview-image`, `POST /quick-translate`, `GET /health` | **Implemented** |
| Pipeline Phase 1 — Detection | **Implemented** (text-layer probe over first 3 pages) |
| Pipeline Phase 2 — Extraction (DIGITAL) | **Implemented** — page size, text blocks, line/span bboxes, font name/size/color/flags, image-block metadata, drawings, best-effort table candidates |
| Pipeline Phase 2 — OCR Service (SCANNED) | **Partially Implemented** — Tesseract/pytesseract wired with grayscale + autocontrast + median denoise, OSD-based 180° flip, per-line confidence, low-confidence warning at threshold 0.55, language-pack fallback to `eng`; PaddleOCR is not wired |
| Pipeline Phase 3 — Language Detection (block-level) | **Partially Implemented** — document-level only today (`langdetect` + Tagabawa dictionary) |
| Pipeline Phase 4 — Translation cascade (exact → normalized → fuzzy → word-by-word) | **Partially Implemented** — exact + fuzzy (rapidfuzz, threshold 82) + word-by-word; the internal `[UNKNOWN_FOR_REVIEW]` marker is set in pipeline metadata, but block-level confidence is not yet surfaced to the frontend |
| Pipeline Phase 4 — Uncertainty Service | **Planned** — no `uncertainty_service.py` |
| Pipeline Phase 5 — Reconstruction (image/line/shape preservation) | **Partially Implemented** — translated text is overlaid on the original PDF page (so most non-text objects survive), but the white mask under each text bbox can still cover decorative elements that overlap a text region |
| Pipeline Phase 5 — Reconstruction (text reinsertion) | **Implemented** — layout-aware: style-aware fontname inference (regular / bold / italic / mono / serif), Unicode TTF fallback with style-matched variants for diacritics, readable minimum font size (≈ 7.5pt floor → 5.5pt hard floor), shrink-then-ellipsis truncation, visible-color forcing over white masks, original-text or `[Needs review]` fallback when insertion fails, and a one-time font-substitution notice. **Exact embedded-font reuse is not guaranteed.** |
| Pipeline Phase 6 — Preview generation | **Implemented** — up to 20 pages rendered as PNG via PyMuPDF |
| Pipeline Phase 7 — Completion check (output exists) | **Implemented** |
| Job queue (Celery/RQ) | **Planned** — currently the pipeline coroutine runs in a worker thread via `asyncio.to_thread` so the HTTP loop stays responsive |
| Object storage (S3/GCS) | **Planned** — currently local filesystem under `tempfile.gettempdir()/bagobo-uploads/jobs/` |
| Database for job tracking | **Planned** — currently in-memory dict; jobs are pre-registered before the background task starts so `/status` answers immediately |
| Pretrained model translation (ByT5 / NLLB) | **Planned** — ByT5-small for Bagobo-Tagabawa directions; NLLB-200 distilled 600M for English/Cebuano/Tagalog; not invoked at runtime; do not train from scratch |

The diagram below shows the **target** architecture. Boxes labelled with services that do not fully exist yet (Uncertainty Service, multi-step Progress Modal component, image/line/shape preservation under text masks) are aspirational. The Side-by-Side Viewer is now implemented as a dedicated route and is no longer a planned component.

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
│  │  Phase 3: Language Detection (during extraction)            │        │
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
│  │  Phase 4: Translation (50%)                                 │        │
│  │  ┌────────────────────────────────────────────────┐         │        │
│  │  │ Translation Dataset Service                    │         │        │
│  │  │ Strategy Cascade (current code):               │         │        │
│  │  │                                                │         │        │
│  │  │ 1. Exact Match           ─→ phrase_index hit   │         │        │
│  │  │ 2. Normalized Match      ─→ lowercase / strip  │         │        │
│  │  │    punctuation / strip diacritics variants     │         │        │
│  │  │ 3. Fuzzy Match           ─→ rapidfuzz ≥ 82     │         │        │
│  │  │ 4. Word-by-Word Fallback ─→ per-token lookup   │         │        │
│  │  │ 5. Internal review marker                      │         │        │
│  │  │    `[UNKNOWN_FOR_REVIEW]` is set in pipeline   │         │        │
│  │  │    metadata when nothing in the cascade        │         │        │
│  │  │    matches. The user-facing label rendered in  │         │        │
│  │  │    the rebuilt PDF is "[Needs review]".        │         │        │
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
│  │  Phase 5: PDF Reconstruction (70%)                          │        │
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
│  │  │   ┌────────────────────────────────────┐       │         │        │
│  │  │   │ • Use original coordinates         │       │         │        │
│  │  │   │ • Style-aware fontname (regular /  │       │         │        │
│  │  │   │   bold / italic / mono / serif)    │       │         │        │
│  │  │   │ • Unicode TTF fallback for         │       │         │        │
│  │  │   │   diacritics (style-matched)       │       │         │        │
│  │  │   │ • Force visible color over white   │       │         │        │
│  │  │   │   mask                             │       │         │        │
│  │  │   │ • Wrap, then shrink toward         │       │         │        │
│  │  │   │   readable min font, then ellipsis │       │         │        │
│  │  │   │ • Restore original line or insert  │       │         │        │
│  │  │   │   "[Needs review]" if unfittable   │       │         │        │
│  │  │   └────────────────────────────────────┘       │         │        │
│  │  │ Note: Exact embedded-font reuse is not         │         │        │
│  │  │ guaranteed; closest available built-in font    │         │        │
│  │  │ is used and a one-time substitution warning    │         │        │
│  │  │ is recorded in `structure.json`.               │         │        │
│  │  └────────────────────────────────────────────────┘         │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  Phase 6: Preview Generation (85%)                          │        │
│  │  Phase 6b: Bilingual Output (95%)                           │        │
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


## Translation Strategy Decision Tree (current code)

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
                ┌──────────┐  ┌──────────────────────────────┐
                │ Return   │  │ Try Normalized Match         │
                │ (exact)  │  │ lowercase, strip punctuation │
                └──────────┘  │ and/or strip diacritics      │
                              └──────┬───────────────────────┘
                                     │
                                Found?│       Not Found
                                 Yes  │          │
                                      ▼          ▼
                               ┌──────────┐  ┌──────────────────┐
                               │ Return   │  │ Try Fuzzy Match  │
                               │ (norm.)  │  │ rapidfuzz ≥ 82   │
                               └──────────┘  └──────┬───────────┘
                                                    │
                                              Found?│       Not Found
                                               Yes  │          │
                                                    ▼          ▼
                                             ┌──────────┐  ┌────────────────────┐
                                             │ Return   │  │ Word-by-Word       │
                                             │ (fuzzy)  │  │ per-token lookup   │
                                             └──────────┘  └──────┬─────────────┘
                                                                  │
                                                            Found?│   Not Found
                                                             Yes  │      │
                                                                  ▼      ▼
                                                          ┌──────────┐  ┌──────────────────────┐
                                                          │ Return   │  │ Internal metadata:   │
                                                          │ (per-    │  │ [UNKNOWN_FOR_REVIEW] │
                                                          │  word)   │  │ User-facing label:   │
                                                          │          │  │ "[Needs review]"     │
                                                          └──────────┘  └──────────────────────┘


## File Storage Structure

jobs/
  {job_id}/
    ├── input_<original_filename>     ← Uploaded file (saved at job root)
    ├── structure.json                ← Page/block structure + warnings
    ├── translated.pdf                ← Final translated PDF
    ├── bilingual.pdf                 ← Side-by-side combined PDF
    └── preview/
        ├── original_page_0.png       ← Preview: Original (up to 20 pages)
        ├── original_page_1.png
        ├── ...
        ├── translated_page_0.png     ← Preview: Translated (up to 20 pages)
        ├── translated_page_1.png
        └── ...


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
  • asyncio.create_task + asyncio.to_thread (development) — the
    pipeline coroutine runs in a worker thread so the HTTP event
    loop stays responsive
  • Celery/RQ (production)


## Key Goal: Layout-Aware Reconstruction

Traditional translation rewrites the entire document. LingoKatutubo *attempts* to preserve the original visual structure:

Target approach:
  1. Separate text from non-text objects
  2. Keep non-text objects at original positions
  3. Translate only text
  4. Place translated text into the original bboxes

> **Honest claim today:** the system performs **partial, layout-aware reconstruction** for digital PDFs. Translated text is overlaid on the original PDF page (so non-text objects survive when they fall outside text bboxes), and the inserter is style-aware: it picks a regular/bold/italic/mono/serif fontname, falls back to a style-matched Unicode TTF for diacritics, enforces a readable minimum font size, shrinks then ellipsizes overflowing text, forces visible color over the white mask, and falls back to the original line or `[Needs review]` when the translation cannot fit. **Exact embedded-font reuse is not guaranteed**; the closest available built-in font is used and a one-time substitution warning is recorded. Decorative elements that overlap a text bbox can still be covered by the white mask. Do not describe the output as a "perfect visual match" or "exact replica" in any defense or report — see [claude.md](claude.md) "Layout preservation truthfulness" and [agent.md](agent.md) "Honest current claim".

This still matters for educational materials because:
  • Diagrams should align with text
  • Cultural symbols should stay intact
  • Page structure aids learning
  • Teacher annotations remain valid

— but those goals are work-in-progress, not delivered guarantees.
