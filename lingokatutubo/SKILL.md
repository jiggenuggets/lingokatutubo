# SKILL.md — LingoKatutubo implementation skill guide

This file explains how to work on the LingoKatutubo system safely and effectively.
It is meant for coding agents and developers who need a practical map of the project.

> **Map the rules below to current code.** Some responsibilities described here (OCR service, side-by-side viewer, uncertainty service, per-block language detection) are **Planned**, not yet implemented. Before you act on any rule that names a module, check whether the file exists. The live status is in [README.md](../README.md).

---

## 1. What the system is
LingoKatutubo is a low-resource, layout-aware document processing and translation system for Bagobo-Tagabawa learning materials and related classroom languages.

Its purpose is not just “translate text.”
Its purpose is to:
- accept school or learning documents
- extract document structure
- preserve visual layout as much as possible
- translate text through a local dataset-first method
- reconstruct a usable output PDF
- support educational preview and download workflows

This means the system is both:
- a document engineering system
- a low-resource translation system

Do not reduce it to only one of those.

---

## 2. Main principle
**Document pipeline first. Translation refinement second.**

If the pipeline is broken, the system is broken even if translation quality is good.
If translation is weak but the pipeline works, the project is still valuable and can improve incrementally.

Always protect:
`upload -> classify -> extract -> preserve -> reconstruct -> preview -> download`

---

## 3. System modules

### Frontend
Expected responsibilities:
- file upload
- language selection or auto-detection display
- job progress display
- side-by-side original vs output preview
- page navigation and zoom
- download action after successful completion
- user-friendly error reporting

Expected non-responsibilities:
- OCR
- heavy PDF parsing
- reconstruction logic
- translation logic

### Backend API
Expected responsibilities:
- receive upload
- create job
- expose job status
- expose preview metadata or images
- expose translated output download
- coordinate pipeline execution

### Pipeline service
Expected responsibilities:
- orchestrate the end-to-end process
- call each phase in order
- track progress percentage and state
- fail safely with explicit reason
- verify output before completion

### Extraction service
For digital PDFs only.
Expected outputs:
- text blocks
- block coordinates
- page dimensions
- non-text objects
- style hints such as font size, alignment, and color if available

### OCR service
For scanned pages only.
Expected outputs:
- OCR text
- bounding boxes
- page index
- confidence if available

### Translation dataset service
Expected behavior:
- phrase lookup first
- normalization second
- fuzzy matching third
- dictionary fallback fourth
- explicit uncertainty behavior for unknowns

### Reconstruction service
Expected behavior:
- preserve page size
- preserve page order
- preserve non-text objects where possible
- place translated text back into matching page regions
- generate final downloadable PDF

### Preview generation
Expected behavior:
- convert original and translated pages to preview images
- support side-by-side viewer
- not block a valid output file unnecessarily if preview partially fails

---

## 4. Canonical processing flow

### Phase 1 — Intake
Input file arrives from frontend.
Store original file safely.
Assign job id.
Create job directory.

### Phase 2 — Document type classification
Decide whether the file is:
- digital PDF / machine-readable text
- scanned PDF / image-like document
- unsupported type

Use native extraction whenever possible.
Do not OCR a digital PDF unless fallback is necessary.

### Phase 3 — Structural extraction
For digital PDFs:
- extract text blocks and regions
- extract non-text items such as images and lines
- preserve page-level geometry
- preserve reading order when possible

For scanned PDFs:
- rasterize page if needed
- run OCR
- produce text with bounding boxes
- keep page references

### Phase 4 — Language detection
Detect source language at document or block level.
Keep this result separate from translation itself.
Language detection should not mutate extracted content.

### Phase 5 — Translation
Translate only text content.
Leave non-text elements untouched.
Use dataset-first logic.
Preserve traceability between source block and translated block.

### Phase 6 — Reconstruction
Rebuild the output using:
- original page dimensions
- preserved non-text objects
- translated text in original or adjusted boxes

### Phase 7 — Preview generation
Create preview images for:
- original document
- translated document

### Phase 8 — Finalization
Verify that output file exists.
Only then mark job complete.

---

## 5. Layout preservation guide

### What “layout-aware” means
It means the system tries to preserve the original visual structure by separating text and non-text content, then reusing original geometry during reconstruction.

It does **not** automatically mean exact or perfect reproduction.

### What should be preserved when possible
- page size
- page order
- text block location
- paragraph grouping
- headings in approximate original place
- images and logos
- lines and borders
- simple shapes
- color and alignment hints

### What is hard and must be treated honestly
- long translated text causing overflow
- multi-column reading order
- tightly packed forms
- complex tables
- embedded fonts
- rotated text
- scanned pages with noisy OCR
- overlapping layers
- mixed writing systems or scripts

### Safe wording for docs and papers
Use:
- partial layout preservation
- layout-aware reconstruction
- coordinate-based text reinsertion
- non-text element retention
- approximate preservation of original visual structure

Avoid unless proven:
- exact visual replication
- pixel-perfect preservation
- perfect table reconstruction
- guaranteed identical formatting

---

## 6. Table handling guide
Tables are not one simple feature.
There are at least three separate cases.

### Case A — Native digital tables
Best case.
You may have enough structure to preserve rows and columns more faithfully.

### Case B — Text aligned to look like tables
Common in PDFs.
Looks like a table but may just be positioned text.
Preservation may be partial and fragile.

### Case C — Scanned tables
Hardest case.
Needs OCR + layout parsing + structure inference.
Do not claim strong support unless explicitly implemented.

### Safe policy
If true table structure is unavailable, preserve what you can and describe it as block-based or approximate table preservation.

---

## 7. OCR guide

### Use OCR only when needed
OCR is for scanned pages or images, not as the first choice for machine-readable PDFs.

### OCR outputs should include
- recognized text
- bounding box
- page index
- confidence if available

### OCR limitations to remember
- noisy line breaks
- wrong characters
- lost reading order
- weak table understanding
- variable font and spacing recovery

### Safe design principle
OCR text is data for reconstruction, not proof of perfect layout.

---

## 8. Translation guide

### Translation is dataset-first
Preferred order:
1. exact phrase match
2. normalized phrase match
3. fuzzy match
4. dictionary fallback
5. unknown marker or review-needed state

### Keep translation modular
Translation must not own:
- OCR logic
- PDF geometry
- frontend state

It should accept text input and return structured translation results with metadata.

### Orthography rules
- preserve Bagobo-Tagabawa spelling faithfully
- preserve accents and glottal markers from trusted references
- do not silently “simplify” indigenous-language text
- prefer uncertainty markers over fabricated translations

---

## 9. Data model expectations
Recommended structured records should contain at least:
- job id
- file type
- page index
- block id
- source text
- translated text
- source language
- target language
- bbox coordinates
- style hints
- translation method
- confidence score
- warning flags

This keeps the pipeline debuggable.

---

## 10. Reconstruction guide

### Reconstruction must do two separate things
1. preserve non-text objects
2. place translated text back into matching layout regions

Do not merge these into one vague rendering step.

### Preferred behavior
- copy or redraw non-text elements first
- then insert translated text
- adjust size or wrapping conservatively if implemented
- verify final output can be opened and previewed

### Common reconstruction risks
- text overflow
- missing images
- wrong page order
- overlapping text and graphics
- broken line spacing
- font mismatch
- missing output file despite job “success”

### Hard rule
Never mark a job complete without checking that the output file exists.

---

## 11. Preview and viewer guide
The preview system is important, not optional.
It proves the result visually.

### Viewer should support
- original vs translated side-by-side
- page navigation
- zoom
- download

### Preview generation should support
- original pages as images
- translated pages as images
- stable file references per job

---

## 12. Logging and observability
Every phase must be visible in logs.
Include:
- job id
- file name
- source/target language
- detected document type
- phase start
- phase finish
- output path
- output existence check
- errors with enough detail to act on

### Good log messages
- `[Translate] created job ...`
- `[Pipeline] extraction started ...`
- `[Pipeline] detected digital PDF`
- `[Reconstruct] output written to ...`
- `[Download] file exists = true`

### Bad log messages
- `failed`
- `done`
- `error happened`

---

## 13. Error handling rules
Frontend errors must be understandable by a non-technical user.
Examples:
- backend server is not running
- missing job id, please translate again
- translated PDF was not created
- OCR failed on this scanned file
- unsupported file type

Backend errors must preserve technical detail in logs.
Do not hide failure causes from logs.

---

## 14. Completion rules
A job is complete only when:
- the pipeline ends successfully
- translated output exists
- state is safe for preview and download

Optional features failing should not corrupt success criteria unless they are core to the promised user flow.

---

## 15. Recommended build order
Use this order unless the project explicitly changes scope:

1. upload and job model
2. status polling
3. digital PDF extraction
4. reconstruction without translation
5. preview generation
6. viewer integration
7. scanned OCR branch
8. image and shape retention improvements
9. table support
10. translation dataset integration
11. uncertainty and review flags
12. optimization and cleanup

This order prevents translation work from hiding document pipeline failures.

---

## 16. What to say in documentation

### Correct claims
- the system supports layout-aware reconstruction
- the system attempts to preserve non-text visual elements
- the system supports OCR-assisted processing for scanned materials
- the system uses a dataset-first translation strategy
- the system provides side-by-side document preview

### Claims to avoid unless proven by testing
- perfect layout preservation
- exact replication of complex documents
- guaranteed table fidelity
- full font preservation in all cases
- flawless OCR for all scans

---

## 17. What counts as a good result
A good result is not “beautiful code.”
A good result is a user being able to:
- upload a real document
- get a valid processed output
- visually compare original and result
- download the output
- trust that the system preserved structure as much as honestly possible

---

## 18. Short execution checklist
Before closing a task, confirm:
- file upload works
- job state updates correctly
- document type detection works
- extraction produced structured data
- translation did not destroy geometry
- reconstruction produced a real PDF
- preview exists or failed safely
- download is blocked until completion
- logs clearly explain what happened

---

## 19. Final rule
When in doubt, choose the safer and more truthful path:
- preserve the working pipeline
- report limitations honestly
- improve one layer at a time
