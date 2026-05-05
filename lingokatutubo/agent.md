# AGENT.md — LingoKatutubo

> **Document type: forward-looking guidance for coding agents.** This file describes how the system is *meant* to behave, not what is fully built today. For the live status table see [README.md](../README.md). Specifically: full side-by-side viewing, the progress modal, PaddleOCR, and pretrained-model translation are **Planned**. Tesseract OCR and first-page bilingual preview are partially implemented.

## Project identity
LingoKatutubo is a layout-aware cross-lingual document learning assistant for Bagobo-Tagabawa and related classroom languages in contextualized IPEd. The system processes uploaded documents, extracts page structure, preserves non-text elements, translates text through a local dataset-driven pipeline, reconstructs the document, and presents side-by-side preview and download outputs.

## Primary goal
Build a stable end-to-end document pipeline first:
1. upload document
2. detect document type
3. extract structure and text
4. preserve layout as much as possible
5. reconstruct output PDF
6. preview original vs translated output
7. only then improve translation coverage and quality

## Current system direction
Frontend:
- Next.js 15 + React 19  *(Implemented)*
- Upload UI  *(Implemented)*
- Progress modal  *(Planned — currently an inline spinner)*
- Side-by-side viewer  *(Partially Implemented — original page preview plus first-page translated blocks; no full viewer component yet)*
- Toolbar for pages, zoom, download  *(Planned — only Download exists)*

Backend:
- FastAPI API  *(Implemented)*
- Async job pipeline  *(Implemented; in-memory job store)*
- PDF extraction  *(Implemented for digital PDFs)*
- PDF reconstruction  *(Partially Implemented — text-only, no shape preservation)*
- OCR branch for scanned documents  *(Partially Implemented — Tesseract/pytesseract, no PaddleOCR)*
- Translation dataset service  *(Implemented — exact + fuzzy + word-by-word)*
- Preview image generation  *(Implemented; frontend consumes original preview and first-page bilingual blocks from `/preview`)*

## Core architecture
### Frontend responsibilities
- Upload files
- Choose or auto-detect language
- Poll job status
- Show progress phases
- Show side-by-side preview
- Allow download only after successful completion

### Backend endpoints
- `POST /translate`
- `GET /status/{id}`
- `GET /preview/{id}`
- `GET /preview-image/{id}/{name}`
- `GET /download/{id}`

### Pipeline phases
1. Detection
   - detect if PDF is digital or scanned
2. Extraction
   - digital: extract text blocks, coordinates, non-text objects, style info
   - scanned: OCR with bounding boxes
3. Language detection
   - detect block or document language
4. Translation
   - exact match
   - normalized match
   - fuzzy match
   - dictionary fallback
   - unknown flag when needed
5. Reconstruction
   - copy non-text objects
   - place translated text back into original regions
6. Preview generation
   - create preview images for original and translated outputs
7. Completion
   - verify output exists before marking job complete

## Non-translation priorities
These should be working before advanced translation work:
- upload flow
- job tracking
- digital vs scanned detection
- digital PDF extraction
- OCR branch
- layout-aware reconstruction
- preview generation
- side-by-side viewer
- download flow

## Layout preservation policy
### Supported goal
The system aims to preserve:
- page size
- page order
- text block positions
- images
- logos
- lines
- simple shapes
- basic font size, color, and alignment where possible

### Honest current claim
Use this wording in code comments, docs, and defense notes:
- “layout-aware reconstruction”
- “attempts to preserve original document structure”
- “preserves non-text elements and reinserts translated text into original regions when possible”

Do **not** claim:
- exact visual match for all files
- perfect table preservation
- perfect OCR layout preservation
- full fidelity for every complex PDF

### Known preservation limits
- text overflow can happen when translation is longer than source
- line wrapping may shift
- multi-column reading order may fail
- tables may degrade into text blocks
- scanned documents lose precision without strong OCR/layout parsing
- font substitution may happen if original font is unavailable
- layering or overlap may break in complex PDFs

## Tables, images, and graphics policy
### Images and non-text objects
Preferred behavior:
- preserve original position
- preserve original size
- preserve original page association
- preserve logos, diagrams, decorative lines, and simple shapes

### Tables
Treat tables in three levels:
1. digital native table structure
   - best case
2. table-like aligned text blocks
   - acceptable fallback
3. scanned table image with OCR boxes
   - weakest case

Always describe current support honestly:
- “basic to partial table preservation depending on source document type”

## OCR policy
### Digital PDF
Use native PDF extraction first.
Do not OCR machine-readable text unless native extraction fails.

### Scanned PDF
Use OCR only when needed.
Expected OCR outputs:
- text
- bounding boxes
- page association
- confidence when available

### OCR caution
OCR is necessary for scanned documents but is not the same as exact layout preservation.

## Translation policy
Translation is dataset-first, not API-first. The current dataset is a 1015-row phrasebook / translation memory, not enough evidence for high-accuracy neural translation claims.
Order of decision:
1. exact phrase match
2. normalized match
3. fuzzy match
4. dictionary word lookup
5. explicit unknown handling

Keep translation separate from extraction and reconstruction logic.
The document pipeline must remain usable even if translation quality is still limited.

## Model preparation policy
ByT5-small is planned for Bagobo-Tagabawa directions. NLLB-200 distilled 600M is planned for English-Cebuano-Tagalog directions. Neither model is loaded or invoked in the active backend. Prepare JSONL translation pairs for future fine-tuning, but do not train from scratch.

## Data and orthography rules
- preserve Bagobo-Tagabawa orthography exactly when sourced from trusted files
- do not silently normalize away accents or glottal markers
- keep source text and translated text traceable to page and block
- do not invent translations for low-confidence text without marking uncertainty
- prefer explicit unknown or review-needed markers over confident hallucination

## Error handling rules
Frontend must show clear user-facing errors for:
- missing job id
- backend unavailable
- output file missing
- OCR failure
- unsupported file type
- extraction failure
- reconstruction failure

Backend must log:
- job id
- file name
- source and target language
- document type
- phase start and finish
- output path
- reconstruction success/failure

## Job status rules
Only mark a job `completed` when:
- processing finished
- output file exists
- preview generation succeeded or failed safely without corrupting job state

Never expose download until output is confirmed.

## Storage expectations
Suggested structure:
- `jobs/{job_id}/input/`
- `jobs/{job_id}/structured/`
- `jobs/{job_id}/translated/`
- `jobs/{job_id}/output/`
- `jobs/{job_id}/preview/`

## Engineering rules
### Always do
- keep modules separated by responsibility
- use typed data models where possible
- keep extraction output structured and serializable
- preserve page-level metadata
- test digital PDFs before scanned PDFs
- test reconstruction before translation tuning
- make fallback paths explicit

### Never do
- mix OCR logic into frontend
- hardcode translation output into layout modules
- claim perfect preservation without proof
- skip verification before completion
- destroy original file references

## Practical build order
1. upload + job creation
2. status polling
3. digital PDF extraction
4. reconstruction without translation
5. preview generation
6. side-by-side viewer
7. OCR branch
8. table handling
9. translation dataset integration
10. uncertainty flags and review flow

## Minimum success criteria
The system is considered functionally working when it can:
- accept a PDF
- classify digital vs scanned
- extract blocks or OCR boxes
- preserve visible non-text objects where possible
- reconstruct a downloadable PDF
- show original and output preview side by side
- translate through a local dataset path

## Research-safe wording
Use these phrases in documentation and reports:
- layout-aware document translation
- partial preservation of visual structure
- non-text object retention
- coordinate-based text reinsertion
- dataset-driven low-resource translation workflow
- OCR-assisted processing for scanned educational materials
