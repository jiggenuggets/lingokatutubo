# CLAUDE.md — Strict implementation guide for LingoKatutubo

You are working on **LingoKatutubo**, a layout-aware cross-lingual document assistant for Bagobo-Tagabawa learning materials.

Your job is to improve the system **without breaking the document pipeline**.

## Top priority
Always protect this flow:
`upload -> detect -> extract -> preserve structure -> reconstruct -> preview -> download`

Translation quality is important, but it is **not** the first thing to stabilize.
The first priority is a working document-processing system.

## Hard rules
- Do not rewrite the whole project without need.
- Do not replace working modules just because another library looks better.
- Do not remove layout-preservation logic to make translation easier.
- Do not claim features are working unless code and output confirm them.
- Do not collapse tables, images, and layout into one vague "OCR works" statement.
- Do not silently change Bagobo-Tagabawa orthography.
- Do not invent translations when the dataset is uncertain.
- Do not mark jobs complete if the output file is missing.

## Core project truth
This project has two different problems:
1. document understanding and reconstruction
2. low-resource translation

Always keep these concerns separated.
A failure in translation should not destroy extraction or reconstruction.

## Required architecture mindset
### Frontend
The frontend should only:
- upload files
- show progress
- display preview
- allow download after completion
- show clear error messages

### Backend
The backend should own:
- document classification
- extraction
- OCR
- translation lookup
- reconstruction
- preview generation
- job persistence/state

## Required processing order
### 1. Detect source document type
Choose first:
- digital PDF
- scanned PDF/image

Never OCR a digital PDF if native extraction is possible.

### 2. Extract structure
For digital PDFs, preserve:
- page size
- text blocks
- coordinates
- images
- lines
- shapes
- style info if available

For scanned PDFs, extract:
- OCR text
- bounding boxes
- confidence when available

### 3. Translate text only
Do not translate images.
Do not translate lines or shapes.
Do not let translation logic modify geometry.

### 4. Reconstruct output
Reinsert translated text into original regions.
Preserve non-text objects at original positions where possible.

### 5. Generate preview
Always generate original and output previews when possible.

## Translation cascade
Use this order unless the project explicitly changes it:
1. exact phrase match
2. normalized match
3. fuzzy match
4. dictionary fallback
5. unknown marker

If confidence is low, prefer a review marker over a false confident answer.

## Layout preservation truthfulness
Allowed wording:
- layout-aware reconstruction
- attempts to preserve layout
- partial preservation of structure
- preserve non-text elements where possible

Forbidden wording unless proven:
- exact replica
- perfect visual match
- fully preserved tables
- guaranteed original formatting in all files

## Tables policy
Treat tables carefully.
There are three cases:
- native digital tables
- text aligned like tables
- scanned tables

Never assume all three behave the same.
If true table preservation is not implemented, say so clearly.

## Images and graphics policy
Images, logos, lines, and simple shapes should be copied or preserved separately from translated text.
Do not rasterize the whole page unless absolutely necessary.
Do not destroy non-text elements just to place translated text.

## OCR policy
OCR is only for scanned input.
OCR output must be treated as noisy.
Do not oversell OCR precision.
If scanned layout is approximate, document that honestly.

## Logging requirements
Every important stage must be visible in logs.
Include:
- job id
- file name
- detected type
- source/target language
- current phase
- failure reason
- output path
- output existence check

## Completion rules
A job may be marked complete only if:
- processing ended successfully
- translated output file exists
- final state is safe to download

## Error message rules
Prefer actionable errors.
Good examples:
- backend not running
- missing job id
- OCR failed on scanned page
- output PDF not created
- unsupported file type
- preview generation failed

Bad examples:
- failed
- unknown error
- something went wrong

## When changing code
Before editing, identify which layer you are touching:
- frontend UI
- API endpoint
- pipeline service
- extraction service
- OCR service
- translation dataset service
- reconstruction service
- preview generation

Then make the smallest reliable change.

## Preferred development order
If several parts are broken, fix in this order:
1. upload and job creation
2. status polling
3. digital extraction
4. reconstruction
5. preview
6. download
7. OCR
8. table handling
9. translation accuracy

## Expected output quality
### Acceptable now
- simple PDF layout mostly preserved
- non-text objects retained where possible
- translation inserted back into approximate original regions
- side-by-side preview works

### Not automatically acceptable
- broken download
- invisible text
- missing images
- overflowed text everywhere
- tables flattened without warning
- scanned layout claimed as exact

## Coding style
- keep functions focused
- name modules by responsibility
- add comments only where useful
- preserve existing working flow
- prefer explicit data models over hidden dict shapes
- keep fallback behavior visible

## What success looks like
A user uploads a PDF and the system can:
- detect whether it is digital or scanned
- extract structure or OCR boxes
- preserve visible page elements where possible
- translate text through the local dataset path
- reconstruct a valid PDF
- show preview and download safely

If you improve the system, improve it toward that outcome.
