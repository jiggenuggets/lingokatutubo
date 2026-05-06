# IMPLEMENTATION_PLAN.md
## LingoKatutubo Implementation Plan

**Project:** LingoKatutubo  
**Goal:** Build a layout-aware document translation system for Bagobo-Tagabawa educational materials.  
**Approach:** Build document processing first, then connect translation datasets later.

> **This is a roadmap, not a status report.** Each phase below is annotated with the labels **Implemented**, **Partially Implemented**, or **Planned** to reflect what exists in code today (2026-05-07). For the live status summary see [README.md](../README.md). For the running system see [RUNNING_GUIDE.md](RUNNING_GUIDE.md).

### Snapshot of plan status

| Phase | Topic | Status |
|-------|-------|--------|
| 1 | Project Foundation (frontend + backend skeleton) | **Implemented** |
| 2 | File Intake / Job Pipeline | **Implemented** (in-memory job store; jobs are pre-registered before background work, pipeline runs in a worker thread via `asyncio.to_thread`; no `job_store.py` module) |
| 3 | PDF Type Detection | **Implemented** (text-layer probe; no pdfplumber) |
| 4 | Digital PDF Extraction | **Implemented** (text + bbox + font name/size/color/flags + image-block metadata + drawings + best-effort table candidates) |
| 5 | Scanned PDF OCR | **Partially Implemented** (Tesseract/pytesseract wired with preprocessing, OSD 180° flip, per-line confidence, low-confidence warning at threshold 0.55, language-pack fallback to `eng`; PaddleOCR not wired) |
| 6 | Layout-Aware Reconstruction | **Implemented** (style-aware fontname inference, Unicode TTF fallback with style-matched variants for diacritics, readable minimum font size, ellipsis truncation, visible-color forcing, original-text/`[Needs review]` fallback, font-substitution notice). Exact embedded-font reuse is **not** guaranteed. Image/shape redraw under text masks is still partial. |
| 7 | Preview Generation | **Implemented** (up to 20 pages per document; frontend viewer paginates with Previous/Next and shows a > 20-page warning) |
| 8 | Tables | **Planned** (best-effort table candidates are emitted in `structure.json`, but layout-faithful table reconstruction is not built) |
| 9 | Translation Dataset Integration | **Implemented** (phrasebook / translation memory; exact → normalized → fuzzy → word-by-word; not enough for high-accuracy neural translation) |
| 10 | Language Detection | **Implemented** at the document level (langdetect + Tagabawa dictionary). **Per-block** detection is still **Planned**. |
| 11 | Frontend Build Order — upload UI | **Implemented** |
| 11 | Frontend Build Order — progress UI | **Implemented** (inline progress card with phase, percent, current step, message; backed by `PIPELINE_PHASES`. A separate modal component is not required by the current flow.) |
| 11 | Frontend Build Order — side-by-side viewer | **Implemented** at the dedicated route `/translate/preview/[jobId]` with Back, Download, Original/Translated images, Page X of N, Previous/Next, and a collapsible Translation Details section. |
| 12 | Pretrained-model translation (ByT5 / NLLB) | **Planned** — data prep (JSONL train/dev/test) is done; no model is loaded or invoked at runtime; do not train from scratch. See [DATASET_MODEL_PREP.md](DATASET_MODEL_PREP.md). |

---

## 1. Build Order Summary

### Phase 1 — Project Foundation
Set up the base frontend and backend structure first.

#### Frontend
- Next.js + React
- Upload page
- Progress modal
- Side-by-side preview page
- Download button
- Status polling

#### Backend
- FastAPI app
- `/translate`
- `/status/{job_id}`
- `/preview/{job_id}`
- `/download/{job_id}`

#### Output of this phase
- User can upload a file
- Backend accepts file
- Job ID is returned
- Frontend can poll job status

---

## 2. Phase 2 — File Intake and Job Pipeline

Create the job pipeline before adding OCR or translation.

### Backend modules
- `main.py`
- `pipeline_service.py`
- `job_store.py`
- `file_service.py`

### Required features
- Save uploaded file
- Create job folder
- Track status
- Track progress percent
- Store output paths
- Return errors cleanly

### Suggested job folder structure
```text
jobs/
  {job_id}/
    input/
    structured/
    translated/
    output/
    preview/
```

### Output of this phase
- Working async job flow
- Clean job state transitions
- Files stored correctly

---

## 3. Phase 3 — PDF Type Detection

Detect whether the input PDF is digital or scanned.

### Module
- `detection_service.py`

### Logic
- If text can be extracted directly → DIGITAL
- If page is mostly image / no real text layer → SCANNED

### Recommended tools
- PyMuPDF
- pdfplumber

### Output of this phase
- System chooses the correct extraction path

---

## 4. Phase 4 — Digital PDF Extraction  *(Partially Implemented)*

> **Reality check:** [extraction_service.py](backend/extraction_service.py) extracts page width/height, text blocks, line bboxes, first-span font name/size/color, span metadata, image-block metadata, and best-effort table/drawing metadata. Alignment is not captured, and reconstruction still does not preserve images/shapes with high fidelity. There is no `layout_models.py`.

This is the first major working path.

### Modules
- `extraction_service.py`  *(exists)*
- `layout_models.py`  *(Planned — typed data model for blocks)*

### Extract these from each page
- page width / height  *(Implemented)*
- text blocks  *(Implemented)*
- bounding boxes  *(Implemented)*
- font size  *(Implemented — first span only)*
- font name  *(Implemented — first span only)*
- font color  *(Implemented — first span only)*
- alignment if possible  *(Planned)*
- images  *(Partial — detected, not preserved through reconstruction)*
- lines  *(Partially Implemented — drawing metadata only)*
- rectangles / shapes  *(Partially Implemented — drawing metadata only)*

### Recommended tools
- pdfplumber
- PyMuPDF

### Output data format
```json
{
  "page_index": 0,
  "width": 612,
  "height": 792,
  "text_blocks": [],
  "non_text_objects": []
}
```

### Output of this phase
- Structured page JSON for digital PDFs
- Coordinates preserved

---

## 5. Phase 5 — Scanned PDF OCR Path  *(Partially Implemented)*

> **Reality check (2026-05-05):** The active SCANNED branch in `pipeline_service.py` calls `backend/ocr_stage/ocr_service.py`, which uses Tesseract through `pytesseract` to rasterize scanned PDFs/images, extract OCR text, bboxes, and confidence, and write warnings into `structure.json`. There is no mock placeholder fallback. If OCR is unavailable or extracts no text, the job fails loudly after saving structure warnings. PaddleOCR is not wired in the active backend.

Only after digital extraction works.

### Modules
- `backend/ocr_stage/ocr_service.py`  *(exists)*
- `image_preprocess.py`  *(Planned — preprocessing currently lives inside OCRService)*

### OCR pipeline
- Convert PDF pages to images
- Preprocess images if needed
- Run OCR
- Return text with bounding boxes

### Recommended tools
- PaddleOCR (preferred)
- Tesseract (backup or baseline)
- Pillow / OpenCV

### Important note
OCR path should be treated as a lower-fidelity path than digital PDFs.

### Output of this phase
- OCR text blocks with bounding boxes
- Basic scanned document handling

---

## 6. Phase 6 — Layout-Aware Reconstruction  *(Implemented, with documented limits)*

> **Reality check:** [reconstruction_service.py](backend/reconstruction_service.py) opens the original PDF, paints a small white mask over each text bbox, and inserts translated text at the original line bbox. The inserter is **style-aware**: it picks a regular/bold/italic/mono/serif fontname from the source span, falls back to a style-matched Unicode TTF (`arialbd.ttf`, `arialbi.ttf`, etc.) when the translation contains diacritics, enforces a readable minimum font size, shrinks then ellipsizes overflowing text, forces a visible color over the white mask, and falls back to the original line or `[Needs review]` if insertion still fails. A one-time font-substitution notice is recorded in `structure.json`. **Exact embedded-font reuse is not guaranteed.** Decorative elements that overlap a text bbox can still be covered by the white mask. A bilingual side-by-side PDF (`bilingual.pdf`) is also produced.

Do this before translation dataset integration.

### Module
- `reconstruction_service.py`  *(exists)*

### Rebuild rules
- Create output PDF page with same size  *(Implemented — opens original doc)*
- Copy images to same location  *(Implemented for non-text objects outside text bboxes; mask can still cover items inside a text bbox)*
- Copy lines/shapes to same location  *(Partially Implemented — same caveat as images)*
- Place translated or original text back into same text boxes  *(Implemented — line-bbox level)*
- Preserve font size where possible  *(Implemented — original size honored when it fits, with a readable minimum floor)*
- Shrink font only if needed  *(Implemented — shrink-then-ellipsis)*
- Wrap text carefully  *(Implemented — paragraph + per-word + per-char wrapping)*

### Important limitation
Do not claim exact perfect layout preservation yet. Exact embedded-font reuse is not guaranteed; the closest available built-in font is used and a substitution warning is recorded.

### Output of this phase
- Rebuilt PDF
- Basic layout-aware output

---

## 7. Phase 7 — Preview Generation  *(Implemented)*

Needed for user trust and debugging.

### Module
- `reconstruction_service.create_preview_images` (no separate `preview_service.py`)

### Preview tasks
- Convert original PDF pages to PNG  *(Implemented — up to 20 pages, prefix `original_page_*.png`)*
- Convert translated PDF pages to PNG  *(Implemented — prefix `translated_page_*.png`)*
- Store preview files under `jobs/{job_id}/preview/`
- Serve through `GET /preview-image/{job_id}/{name}` with regex-restricted filenames

### Recommended tools
- PyMuPDF (already in use)

### Output of this phase
- Side-by-side preview is rendered by `/translate/preview/[jobId]` with Page X of N navigation. Documents longer than 20 pages display a banner directing the user to download the full translated PDF for the remaining pages.

---

## 8. Phase 8 — Tables

Add tables after base extraction/reconstruction works.

### Digital PDFs
Use:
- Camelot
- pdfplumber table extraction

### Scanned PDFs
Use later if needed:
- Table Transformer
- PaddleOCR table modules

### Honest rule
At first, tables can be treated as text blocks if full structure preservation is not ready.

### Output of this phase
- Better handling of structured educational materials
- Clear distinction between simple and complex tables

---

## 9. Phase 9 — Translation Dataset Integration

Do this after the document engine already works.

### Modules
- `translation_dataset.py`
- `dictionary_service.py`
- `matching_service.py`

### Translation cascade
1. Exact phrase match
2. Normalized phrase match
3. Fuzzy match
4. Dictionary fallback
5. Unknown flag

### Dataset/model reality
- The active dataset is `backend/translation_data.json` with 1015 phrase rows, not exactly 900.
- Use it first as a phrasebook / translation memory.
- Do not claim this dataset is enough for high-accuracy neural translation.
- Future model preparation should export directional JSONL pairs before any fine-tuning work.
- ByT5-small is planned for Bagobo-Tagabawa directions.
- NLLB-200 distilled 600M is planned for English-Cebuano-Tagalog directions.
- Do not train from scratch; only fine-tune/adapt pretrained models after the document pipeline and review workflow are stable.

### Why translation comes later
Because document processing and layout preservation are separate hard problems.
Solve the document system first.

### Output of this phase
- Real multilingual text replacement
- Translation attached to existing pipeline

---

## 10. Phase 10 — Language Detection

### Module
- `language_detection_service.py`

### Supported languages
- English
- Filipino
- Cebuano
- Bagobo-Tagabawa

### Use
- detect source language
- detect block language if needed
- support mixed-language warnings

### Output of this phase
- Better routing into translation dataset
- Better system transparency

---

## 11. Frontend Build Order

### Step 1
Upload page
- drag and drop
- file picker
- target language selector
- translate button

### Step 2
Progress modal
- queued
- detecting
- extracting
- OCR
- translating
- reconstructing
- previewing
- completed

### Step 3
Preview page
- original preview left
- translated preview right
- page controls
- zoom controls
- download button

### Step 4
Error UI
- missing file
- backend down
- output not found
- OCR failed
- reconstruction failed

---

## 12. Minimum Viable Product

Your MVP should be:

### Must work
- Upload digital PDF
- Detect source type
- Extract text blocks and layout
- Reconstruct output PDF
- Show preview
- Download result

### Can be limited
- OCR for scanned PDFs
- complex tables
- multilingual mixed pages
- exact font match
- bilingual output

---

## 13. Honest Capability Statement

At MVP stage, your system should be described as:

> A layout-aware document translation prototype that extracts text and page structure from educational materials, reconstructs translated output while attempting to preserve the original layout, and supports future improvement for OCR, tables, and advanced translation quality.

Do **not** claim:
- perfect layout preservation
- exact table preservation in all cases
- exact visual duplication for every PDF
- full scanned document fidelity

---

## 14. Suggested Folder Structure

```text
backend/
  main.py
  services/
    pipeline_service.py
    detection_service.py
    extraction_service.py
    ocr_service.py
    reconstruction_service.py
    preview_service.py
    language_detection_service.py
    translation_dataset.py
    dictionary_service.py
    matching_service.py
    uncertainty_service.py
  models/
    layout_models.py
    job_models.py
  storage/
    job_store.py
  utils/
    file_service.py
    text_normalizer.py

frontend/
  app/
    translate/
      page.tsx
  components/
    UploadPanel.tsx
    ProgressModal.tsx
    SideBySideViewer.tsx
    Toolbar.tsx
    ErrorBanner.tsx
```

---

## 15. Testing Order

### Test 1
Simple digital PDF with plain paragraphs

### Test 2
Digital PDF with headings and bullets

### Test 3
Digital PDF with image and line separators

### Test 4
Digital PDF with simple table

### Test 5
Scanned PDF with clear text

### Test 6
Mixed layout educational material

### Test 7
Long translated text overflow

---

## 16. Priority List

### Highest Priority
- upload flow
- job pipeline
- digital PDF extraction
- reconstruction
- preview
- download

### Medium Priority
- OCR
- language detection
- better text fitting
- simple tables

### Lower Priority
- bilingual PDF
- advanced table structure
- multi-column perfection
- exact font substitution

---

## 17. Recommendation for Your Capstone

For capstone success, focus first on:
- **digital PDF path**
- **basic layout preservation**
- **preview + reconstruction**
- **clear honest limitations**

Then add:
- OCR
- tables
- translation dataset improvements

This gives you a working system faster and makes your defense stronger because the core pipeline is visible and testable.
