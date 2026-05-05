# Tesseract OCR Language Packs

LingoKatutubo uses `pytesseract.get_languages()` at runtime to check which
Tesseract language packs are installed. The backend does not install language
packs automatically.

## Current OCR Mapping

| Requested language | Tesseract pack used |
|---|---|
| `english` | `eng` |
| `filipino` | `fil` when installed, otherwise `tgl` when installed |
| `tagalog` | `tgl` |
| `cebuano` | `ceb` |
| `bagobo`, `tagabawa`, `bagobo-tagabawa` | `eng` fallback with warning |

There is no known Tesseract language model for Bagobo/Tagabawa in this project
yet, so OCR falls back to English and records a warning in `structure.json`.

## Manual Installation

Install only the packs you need, then restart the backend process:

- English: `eng.traineddata`
- Filipino: `fil.traineddata`
- Tagalog: `tgl.traineddata`
- Cebuano: `ceb.traineddata`
- Orientation detection: `osd.traineddata`

On Windows, these files usually live in a Tesseract `tessdata` directory such
as `C:\Program Files\Tesseract-OCR\tessdata`. If Tesseract is installed in a
custom location, set `TESSERACT_CMD` to the `tesseract.exe` path and/or
`TESSDATA_PREFIX` to the tessdata directory before starting the backend.

If a requested pack is missing, the backend uses `eng` instead and adds a
warning to the saved `structure.json` for the job.
