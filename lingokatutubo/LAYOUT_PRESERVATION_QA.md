# Layout Preservation QA Checklist

## Honest baseline claim

LingoKatutubo provides **best-effort layout preservation with measurable QA
checks** — not 100% layout preservation, not an exact replica, and not a
guarantee of pixel-perfect output. Translated text is reinserted into the
original document's text regions; non-text elements (images, lines, simple
shapes) are kept on the original page wherever possible.

Do not describe the output as "100% accurate," "exact layout," "perfect
reconstruction," "exact replica," or "guaranteed original formatting" in any
report, demo, or defense. See [claude.md](claude.md) ("Layout preservation
truthfulness"), [agent.md](agent.md), [SKILL.md](SKILL.md), and
[ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md) for the project's existing
wording rules — this document follows the same standard and does not loosen
it.

## How to use this checklist

This is a **manual QA checklist** for reviewing a translated/reconstructed
document before calling a job's output acceptable. It is not (yet) a fully
automated test suite — some items are enforced in code today (noted below),
others require a human to open the bilingual preview or the output PDF and
look. Treat an unchecked or "not implemented" item as an honest gap, not a
bug to hide.

## Checklist

| # | Criterion | Current status | Notes |
|---|-----------|-----------------|-------|
| 1 | Same page count (output vs. source) | **Automated** | `PipelineService._require_pdf_page_count` raises rather than completing a job whose translated PDF page count doesn't match the source; the bilingual side-by-side PDF is skipped (not silently mismatched) if original/translated page counts differ. |
| 2 | Same page order | **Automated by construction** | Pages are processed and reinserted in a single ordered loop (page 0, 1, 2, ...); there is no reordering step. Not separately verified by an automated order-check beyond this structural guarantee. |
| 3 | Original images remain visible | **Mostly automated, one known exception** | Reconstruction overlays translated text on the *original* PDF page rather than a blank canvas, so non-text objects survive by default. Exception: a decorative element that overlaps a text bounding box can still be covered by the white mask used to clear the original text before reinsertion. |
| 4 | Image positions remain close to original | **Automated by construction** | Images are never moved or re-laid-out; only text regions are masked and replaced in place. |
| 5 | Translated text appears in the correct original text region | **Automated by construction** | Reinsertion uses the original block/line bounding boxes from extraction; translated text is drawn into the same coordinates the source text occupied. |
| 6 | Similar font size where possible | **Partially automated** | A readable minimum font size is enforced; oversized/overflowing text is shrunk before falling back to ellipsis. Exact original point size is not always reproducible (see font fallback below). |
| 7 | Similar font weight where possible | **Partially automated** | Bold/italic/mono/serif is detected from the source line and mapped to the closest matching base font family; exact weight matching to the original embedded font is not guaranteed. |
| 8 | Fallback font documented when original font is unavailable | **Documented** | Exact embedded-font reuse is not guaranteed. The system picks the closest available built-in font (or a style-matched Unicode font for diacritics) and records a one-time substitution warning. See `ARCHITECTURE_DIAGRAM.md`'s "Honest claim today" note. |
| 9 | No missing text (page level) | **Automated** | A job fails rather than completing silently if a page's text could not be extracted/translated, or if the output PDF's page count doesn't match the source (see #1). |
| 10 | No missing text (line/block level) | **Manual review only** | There is no automated "every source line has a corresponding translated line" verification today. Reviewers should check the bilingual preview's segment list for unexpectedly short or empty translated text. |
| 11 | No overlapping text | **Partially mitigated, not formally verified** | Overflow text is shrunk then ellipsized rather than left to overlap neighboring content, but this is not validated with automated visual-regression testing. Manual review recommended for dense or multi-column source pages. |
| 12 | No clipped text | **Partially mitigated, not formally verified** | Same shrink/ellipsis mitigation as #11. Very long translations in narrow regions may still visually clip in edge cases; not yet covered by an automated check. |
| 13 | No text placed outside page boundaries | **Manual review only** | Reinsertion uses original in-page coordinates, so this is unlikely in practice, but there is no automated page-boundary assertion in the test suite today. |
| 14 | Table/cell boundaries preserved where possible | **Not implemented** | There is no dedicated table-detection or cell-boundary-preservation logic in `reconstruction_service.py`. Tables (native digital tables, text aligned like tables, and scanned tables) are all currently treated as ordinary text blocks. State this honestly in any report — do not describe table output as "preserved." |
| 15 | Limitations are clearly documented | **This document, plus existing project docs** | See "Known limitations" below and the cross-referenced docs above. |

## Known limitations

- **Tables are not specially handled.** All three table cases (native digital, text-aligned, scanned) are reconstructed as plain text blocks. If a job's source document relies on table structure for meaning, expect that structure to be lost in the output even though the text itself is translated.
- **Exact font reuse is not guaranteed.** The system substitutes the closest available built-in/fallback font and logs a one-time warning; it does not embed or re-derive the original document's exact font file.
- **No automated visual-regression testing exists yet.** There is no screenshot-diffing or rendered-page comparison step in the test suite. All "no overlap / no clipping / no out-of-bounds text" guarantees below the page-count level are currently enforced by mitigation logic (shrink-then-ellipsize, in-place coordinate reuse), not by automated visual verification.
- **Line/block-level text loss is not automatically detected.** Page-level loss causes a job failure (see checklist #1/#9), but a single dropped or truncated line within an otherwise-successful page is not automatically flagged — only caught by manual review of the bilingual preview.
- **Decorative elements overlapping a text region can be covered.** The white mask used to clear original text before reinsertion does not distinguish "background decoration" from "blank page area" — anything under a text bounding box is masked.

## Recommended manual QA pass (until automated checks exist)

For a representative sample of completed jobs (especially multi-page PDFs,
scanned documents, and anything with tables), open the bilingual preview and
the downloaded output and check, page by page:

1. Page count and order match the source.
2. Every visible image/logo/line from the source is still present and in roughly the same place.
3. Translated text sits where the original text was, not drifted elsewhere on the page.
4. No paragraph clearly cuts off mid-sentence or runs past the page edge.
5. No two text blocks visibly overlap.
6. If the source had a table, note explicitly that table structure is not preserved (per the limitation above) rather than describing it as working.

## Related documents

- [claude.md](claude.md) — "Layout preservation truthfulness," "Tables policy," forbidden/allowed wording lists.
- [agent.md](agent.md) — honest current-state claims for OCR and reconstruction.
- [SKILL.md](SKILL.md) — "Claims to avoid unless proven by testing."
- [ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md) — "Honest claim today" note on font substitution and white-mask behavior.
