# LingoKatutubo - Implementation Guide
## Layout-Preserving PDF Translation System

**Version:** 1.1  
**Last reviewed:** 2026-05-04  
**Status:** Reference / forward-looking. Most code samples below are **Planned** and not yet present in the repo. See [README.md](../README.md) for the live status table.

> ⚠️ **How to read this file.** This is a target-state implementation reference. The Python and TypeScript snippets here are **design samples**, not the current code. Where the current code differs, follow the reality notes inline.

---

## Executive Summary

✅ **Implemented**
- Frontend upload UI ([frontend/app/translate/page.tsx](frontend/app/translate/page.tsx))
- FastAPI backend with `/translate`, `/status`, `/preview`, `/download`, `/quick-translate`, `/health`, `/preview-image` ([backend/main.py](backend/main.py))
- Translation dataset loader with exact + fuzzy (rapidfuzz) + word-by-word fallback ([backend/translation_dataset.py](backend/translation_dataset.py))
- File upload + job directory + in-memory job tracking
- Digital PDF text + bbox extraction (PyMuPDF), DOCX paragraph extraction
- Document-level language detection (langdetect + Tagabawa dictionary)
- Translated PDF reconstruction (white-box overlay + 11pt insert_text)
- Bilingual PDF (side-by-side combined)
- PNG previews for first 2 pages (endpoint exists, frontend does not consume it)

⚠️ **Partially Implemented**
- PDF reconstruction (no font/size/color adaptation, no shape preservation, no overflow handling)
- Translation cascade (no `[UNKNOWN]` marker, no confidence surfaced to frontend)
- Image-block extraction (detected but not redrawn during reconstruction)

❌ **Planned — Not Yet Implemented (in priority order)**
- **OCR for scanned documents** (Phase 3.1 below — sample code only; no `ocr_service.py` exists; `pytesseract` is unused; `backend/ocr_stage/` is empty)
- **Structured per-block JSON endpoint** for the bilingual UI
- **Side-by-Side Translation Preview** in the frontend (no `translation-viewer.tsx`)
- **Document Translation Progress Modal** (no `translation-progress-modal.tsx`)
- **Per-block language detection** (today's detection runs over the document, not per block)
- **Uncertainty flagging service** (no `uncertainty_service.py`)
- **Pretrained model translation** (ByT5-small for Bagobo, NLLB-200 distilled 600M for English/Cebuano/Tagalog) — no model is loaded or invoked at runtime
- **Layout-preserving font/color/alignment, multi-line wrapping, image/shape redraw**

---

## Current System Analysis (verified 2026-05-04)

### Backend Architecture (Current State)

```
backend/
├── main.py                          # ✅ FastAPI app + CORS + endpoints
├── models.py                        # ✅ Pydantic models (FileType, JobStatus, etc.)
├── file_service.py                  # ✅ File storage + job dir
├── pipeline_service.py              # ⚠️  Async pipeline; SCANNED branch is mocked
├── extraction_service.py            # ⚠️  Text/bbox/font-name only; no color/alignment/shapes
├── reconstruction_service.py        # ⚠️  White-box + 11pt insert_text; no shape preservation
├── detection_service.py             # ✅ Digital vs scanned (text-layer probe)
├── translation_dataset.py           # ✅ Exact + rapidfuzz fuzzy + word-by-word
├── language_detection_service.py    # ✅ langdetect + Tagabawa dictionary
├── translation_data.json            # ✅ Phrase dataset
├── ocr_stage/                       # ❌ EMPTY — reserved for OCR (Planned)
└── requirements.txt                 # ⚠️  pytesseract listed but never imported
```

### Frontend Architecture (Current State)

```
frontend/
├── app/
│   ├── page.tsx                # ✅ Home
│   ├── translate/page.tsx      # ⚠️  Upload + status polling + download (no preview)
│   └── about/page.tsx          # ✅ About page
├── components/
│   ├── navigation.tsx          # ✅ Navigation
│   ├── theme-provider.tsx      # ✅
│   └── ui/                     # shadcn primitives — most are unused
└── hooks/use-upload.ts         # ✅ POST /translate

# ❌ Missing (Planned):
#   components/translation-viewer.tsx
#   components/translation-progress-modal.tsx
```

---

## Implementation Roadmap

### Phase 1: Core Translation Pipeline (Week 1-2)

#### 1.1 Enhanced PDF Extraction with Layout Preservation

**Goal:** Extract text blocks with exact coordinates, preserve non-text elements

**File:** `backend/extraction_service.py`

**Current Issues:**
- Basic text extraction only
- No separation of text vs non-text objects
- Missing coordinate precision

**Implementation:**

```python
# Enhanced extraction structure
class LayoutBlock:
    """Represents a layout block with preserved coordinates"""
    block_id: str
    page_number: int
    type: str  # 'text', 'image', 'line', 'shape', 'table'
    bbox: Tuple[float, float, float, float]  # (x, y, width, height)
    content: Optional[str]
    metadata: Dict[str, Any]  # font, color, etc.

def extract_pdf_with_layout_preservation(pdf_path: str) -> List[PageLayout]:
    """
    Extract PDF with complete layout preservation
    
    Returns:
        List of PageLayout objects containing:
        - Text blocks with coordinates
        - Image objects with coordinates
        - Vector graphics (lines, shapes)
        - Table regions
        - Original page dimensions
    """
    pages = []
    doc = fitz.open(pdf_path)
    
    for page_num in range(doc.page_count):
        page = doc[page_num]
        
        # Extract different object types
        text_blocks = extract_text_blocks(page)
        image_objects = extract_images(page)
        vector_objects = extract_vector_graphics(page)
        
        pages.append(PageLayout(
            page_number=page_num,
            width=page.rect.width,
            height=page.rect.height,
            text_blocks=text_blocks,
            non_text_objects=image_objects + vector_objects
        ))
    
    return pages
```

**Key Features to Add:**
1. Separate text blocks from logos/images/lines
2. Store exact font information (name, size, color)
3. Detect table regions
4. Preserve vector graphics coordinates
5. Track page metadata (orientation, dimensions)

#### 1.2 Enhanced Translation with Fuzzy Matching

**File:** `backend/translation_dataset.py`

**Current Issues:**
- Only exact and word-level matching
- No fuzzy matching
- No confidence scoring
- Missing dictionary fallback integration

**Implementation:**

```python
from fuzzywuzzy import fuzz
from typing import Tuple

class TranslationResult:
    """Result of translation attempt"""
    original: str
    translated: str
    method: str  # 'exact', 'normalized', 'fuzzy', 'dictionary', 'unknown'
    confidence: float  # 0.0 to 1.0
    warnings: List[str]

def translate_with_confidence(
    text: str,
    source_lang: str = "english",
    target_lang: str = "tagabawa"
) -> TranslationResult:
    """
    Translate with multiple strategies and confidence scoring
    
    Strategies (in order):
    1. Exact phrase match (confidence: 1.0)
    2. Normalized match (lowercase, strip punctuation) (confidence: 0.95)
    3. Fuzzy match (> 85% similarity) (confidence: 0.7-0.9)
    4. Dictionary word lookup (confidence: 0.6)
    5. Unknown flag (confidence: 0.0)
    """
    
    # Strategy 1: Exact match
    exact_match = self.phrase_index.get(text.strip())
    if exact_match:
        return TranslationResult(
            original=text,
            translated=exact_match[0][f"{target_lang}_source"],
            method="exact",
            confidence=1.0,
            warnings=[]
        )
    
    # Strategy 2: Normalized match
    normalized = normalize_text(text)
    normalized_match = self.phrase_index.get(normalized)
    if normalized_match:
        return TranslationResult(
            original=text,
            translated=normalized_match[0][f"{target_lang}_source"],
            method="normalized",
            confidence=0.95,
            warnings=[]
        )
    
    # Strategy 3: Fuzzy match
    best_fuzzy_match, score = find_fuzzy_match(text, self.phrase_index)
    if score > 85:
        return TranslationResult(
            original=text,
            translated=best_fuzzy_match[f"{target_lang}_source"],
            method="fuzzy",
            confidence=score / 100.0,
            warnings=[f"Fuzzy match: {score}% similar to '{best_fuzzy_match['english_source']}'"]
        )
    
    # Strategy 4: Dictionary fallback
    dictionary_result = self.dubois_dictionary_lookup(text, target_lang)
    if dictionary_result:
        return TranslationResult(
            original=text,
            translated=dictionary_result,
            method="dictionary",
            confidence=0.6,
            warnings=["Word-level dictionary translation"]
        )
    
    # Strategy 5: Unknown
    return TranslationResult(
        original=text,
        translated=f"[UNKNOWN: {text}]",
        method="unknown",
        confidence=0.0,
        warnings=["No translation found"]
    )
```

**Key Features to Add:**
1. Fuzzy string matching with `fuzzywuzzy` or `rapidfuzz`
2. Normalized text matching (lowercase, remove punctuation)
3. Confidence scoring for each translation
4. DuBois dictionary integration
5. `[UNKNOWN]` and `[UNCERTAIN]` flags

#### 1.3 Layout-Preserving PDF Reconstruction

**File:** `backend/reconstruction_service.py`

**Current Issues:**
- Creates new PDF from scratch
- Doesn't preserve original layout
- Missing logo/image/line preservation

**Implementation:**

```python
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import fitz  # PyMuPDF

def reconstruct_pdf_with_layout(
    original_pdf_path: str,
    layout_data: List[PageLayout],
    translations: Dict[str, TranslationResult],
    output_path: str
) -> bool:
    """
    Reconstruct PDF preserving original layout
    
    Process:
    1. Create new PDF with same dimensions
    2. Copy non-text objects (images, lines, shapes) to exact positions
    3. Place translated text in original text box coordinates
    4. Adjust font size if translated text is longer
    5. Preserve colors, fonts, and styling
    """
    
    original_doc = fitz.open(original_pdf_path)
    new_doc = fitz.open()
    
    for page_layout in layout_data:
        page_num = page_layout.page_number
        original_page = original_doc[page_num]
        
        # Create new page with same dimensions
        new_page = new_doc.new_page(
            width=page_layout.width,
            height=page_layout.height
        )
        
        # Step 1: Copy all non-text objects
        for obj in page_layout.non_text_objects:
            if obj.type == 'image':
                copy_image_to_page(new_page, obj, original_page)
            elif obj.type == 'line':
                draw_line_on_page(new_page, obj)
            elif obj.type == 'shape':
                draw_shape_on_page(new_page, obj)
        
        # Step 2: Insert translated text at original coordinates
        for text_block in page_layout.text_blocks:
            original_text = text_block.content
            translation_result = translations.get(original_text)
            
            if translation_result:
                insert_text_with_layout_preservation(
                    new_page,
                    text_block,
                    translation_result,
                    adjust_font_size=True
                )
    
    new_doc.save(output_path)
    new_doc.close()
    original_doc.close()
    return True

def insert_text_with_layout_preservation(
    page: fitz.Page,
    text_block: TextBlock,
    translation: TranslationResult,
    adjust_font_size: bool = True
):
    """
    Insert translated text into original text box coordinates
    
    Features:
    - Preserves original font (or uses closest match)
    - Adjusts font size if translated text is longer
    - Maintains text alignment
    - Preserves color
    """
    
    bbox = text_block.bbox
    original_text = text_block.content
    translated_text = translation.translated
    
    # Calculate if text fits
    original_length = len(original_text)
    translated_length = len(translated_text)
    
    # Adjust font size if needed
    base_font_size = text_block.metadata.get('font_size', 11)
    if translated_length > original_length * 1.2:
        font_size = base_font_size * (original_length / translated_length) * 0.9
    else:
        font_size = base_font_size
    
    # Insert text with proper formatting
    page.insert_textbox(
        bbox,
        translated_text,
        fontsize=font_size,
        fontname=text_block.metadata.get('font_name', 'helv'),
        color=text_block.metadata.get('color', (0, 0, 0)),
        align=text_block.metadata.get('align', 0)  # 0=left, 1=center, 2=right
    )
```

**Key Features to Add:**
1. Copy non-text objects to exact positions (images, logos, lines)
2. Insert translated text in original text box coordinates
3. Intelligent font size adjustment for longer translations
4. Preserve font family, color, alignment
5. Handle multi-line text wrapping

---

### Phase 2: Side-by-Side Preview UI (Week 2-3)

#### 2.1 Backend: Preview Image Generation

**File:** `backend/reconstruction_service.py`

**Add Method:**

```python
def create_preview_images(
    pdf_path: str,
    preview_dir: str,
    max_pages: int = 5,
    dpi: int = 150
) -> List[str]:
    """
    Create PNG preview images from PDF pages
    
    Args:
        pdf_path: Path to PDF file
        preview_dir: Directory to save preview images
        max_pages: Maximum number of pages to preview
        dpi: Resolution (150 for web preview, 300 for print)
    
    Returns:
        List of preview image paths
    """
    os.makedirs(preview_dir, exist_ok=True)
    
    doc = fitz.open(pdf_path)
    preview_paths = []
    
    for page_num in range(min(doc.page_count, max_pages)):
        page = doc[page_num]
        
        # Render at specified DPI
        zoom = dpi / 72  # 72 DPI is default
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        # Save as PNG
        preview_path = os.path.join(preview_dir, f"page_{page_num}.png")
        pix.save(preview_path)
        preview_paths.append(preview_path)
    
    doc.close()
    return preview_paths
```

#### 2.2 Frontend: Side-by-Side Viewer Component

**New File:** `frontend/components/translation-viewer.tsx`

```typescript
'use client';

import { useState, useEffect } from 'react';
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut, Download, Edit } from 'lucide-react';

interface TranslationViewerProps {
  jobId: string;
  onClose?: () => void;
}

export function TranslationViewer({ jobId, onClose }: TranslationViewerProps) {
  const [currentPage, setCurrentPage] = useState(0);
  const [pageCount, setPageCount] = useState(0);
  const [originalPages, setOriginalPages] = useState<string[]>([]);
  const [translatedPages, setTranslatedPages] = useState<string[]>([]);
  const [zoom, setZoom] = useState(100);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    fetchPreview();
  }, [jobId]);

  const fetchPreview = async () => {
    try {
      const response = await fetch(`/api/preview/${jobId}`);
      const data = await response.json();
      
      setOriginalPages(data.original_pages);
      setTranslatedPages(data.translated_pages);
      setPageCount(data.page_count);
      setIsLoading(false);
    } catch (error) {
      console.error('Failed to load preview:', error);
      setIsLoading(false);
    }
  };

  const handleDownload = async () => {
    const link = document.createElement('a');
    link.href = `/api/download/${jobId}`;
    link.download = `translated_${jobId}.pdf`;
    link.click();
  };

  if (isLoading) {
    return <LoadingSpinner />;
  }

  return (
    <div className="fixed inset-0 bg-background z-50">
      {/* PDF Viewer Toolbar */}
      <div className="h-16 border-b bg-white flex items-center justify-between px-6">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-bold">Side-by-Side Translation Preview</h2>
        </div>

        <div className="flex items-center gap-2">
          {/* Page Navigation */}
          <button
            onClick={() => setCurrentPage(Math.max(0, currentPage - 1))}
            disabled={currentPage === 0}
            className="p-2 rounded hover:bg-gray-100 disabled:opacity-50"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          
          <span className="text-sm">
            Page {currentPage + 1} of {pageCount}
          </span>
          
          <button
            onClick={() => setCurrentPage(Math.min(pageCount - 1, currentPage + 1))}
            disabled={currentPage === pageCount - 1}
            className="p-2 rounded hover:bg-gray-100 disabled:opacity-50"
          >
            <ChevronRight className="w-5 h-5" />
          </button>

          {/* Zoom Controls */}
          <div className="mx-4 border-x px-4 flex items-center gap-2">
            <button
              onClick={() => setZoom(Math.max(50, zoom - 25))}
              className="p-2 rounded hover:bg-gray-100"
            >
              <ZoomOut className="w-5 h-5" />
            </button>
            <span className="text-sm w-12 text-center">{zoom}%</span>
            <button
              onClick={() => setZoom(Math.min(200, zoom + 25))}
              className="p-2 rounded hover:bg-gray-100"
            >
              <ZoomIn className="w-5 h-5" />
            </button>
          </div>

          {/* Download Button */}
          <button
            onClick={handleDownload}
            className="px-4 py-2 bg-primary text-white rounded-lg flex items-center gap-2 hover:bg-primary/90"
          >
            <Download className="w-4 h-4" />
            Download
          </button>

          {/* Close Button */}
          {onClose && (
            <button
              onClick={onClose}
              className="px-4 py-2 border rounded-lg hover:bg-gray-100"
            >
              Close
            </button>
          )}
        </div>
      </div>

      {/* Parallel Document Viewer */}
      <div className="h-[calc(100vh-4rem)] flex overflow-hidden">
        {/* Original Document */}
        <div className="flex-1 border-r overflow-auto bg-gray-50 p-6">
          <div className="max-w-3xl mx-auto">
            <div className="mb-2 text-sm font-semibold text-gray-600">
              Original Document
            </div>
            {originalPages[currentPage] && (
              <img
                src={`/api/preview-image/${jobId}/${originalPages[currentPage]}`}
                alt={`Original page ${currentPage + 1}`}
                className="w-full shadow-lg"
                style={{ transform: `scale(${zoom / 100})`, transformOrigin: 'top center' }}
              />
            )}
          </div>
        </div>

        {/* Translated Document */}
        <div className="flex-1 overflow-auto bg-gray-50 p-6">
          <div className="max-w-3xl mx-auto">
            <div className="mb-2 text-sm font-semibold text-gray-600">
              Translated Document
            </div>
            {translatedPages[currentPage] && (
              <img
                src={`/api/preview-image/${jobId}/${translatedPages[currentPage]}`}
                alt={`Translated page ${currentPage + 1}`}
                className="w-full shadow-lg"
                style={{ transform: `scale(${zoom / 100})`, transformOrigin: 'top center' }}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
```

#### 2.3 Document Translation Progress Modal

**New File:** `frontend/components/translation-progress-modal.tsx`

```typescript
'use client';

import { useEffect, useState } from 'react';
import { Loader2, Check, AlertCircle } from 'lucide-react';

interface ProgressModalProps {
  jobId: string;
  onComplete: () => void;
}

const STAGES = [
  { key: 'queued', label: 'Queue', description: 'Waiting in queue...' },
  { key: 'parsing', label: 'Parse', description: 'Reading document structure...' },
  { key: 'ocr', label: 'OCR', description: 'Extracting text...' },
  { key: 'translation', label: 'Trans.', description: 'Translating content...' },
  { key: 'layout', label: 'Layout', description: 'Rebuilding document...' },
];

export function TranslationProgressModal({ jobId, onComplete }: ProgressModalProps) {
  const [status, setStatus] = useState<'processing' | 'completed' | 'failed'>('processing');
  const [progress, setProgress] = useState(0);
  const [currentStage, setCurrentStage] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const pollStatus = async () => {
      try {
        const response = await fetch(`/api/status/${jobId}`);
        const data = await response.json();

        setStatus(data.status);
        setProgress(data.progress);
        
        // Map progress to stage
        if (data.progress < 10) setCurrentStage(0);
        else if (data.progress < 25) setCurrentStage(1);
        else if (data.progress < 50) setCurrentStage(2);
        else if (data.progress < 75) setCurrentStage(3);
        else if (data.progress < 100) setCurrentStage(4);
        else setCurrentStage(5);

        if (data.status === 'completed') {
          onComplete();
        } else if (data.status === 'failed') {
          setError(data.error || 'Translation failed');
        }
      } catch (err) {
        console.error('Failed to poll status:', err);
      }
    };

    const interval = setInterval(pollStatus, 1000);
    pollStatus(); // Initial call

    return () => clearInterval(interval);
  }, [jobId, onComplete]);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl p-8 max-w-md w-full shadow-2xl">
        <h3 className="text-2xl font-bold mb-6 text-center">
          Document Translation Progress
        </h3>

        {/* Multi-step Progress Indicator */}
        <div className="mb-8">
          <div className="flex justify-between mb-4">
            {STAGES.map((stage, index) => (
              <div key={stage.key} className="flex flex-col items-center">
                <div
                  className={`w-10 h-10 rounded-full flex items-center justify-center mb-2 transition-all ${
                    index < currentStage
                      ? 'bg-green-500 text-white'
                      : index === currentStage
                      ? 'bg-primary text-white animate-pulse'
                      : 'bg-gray-200 text-gray-400'
                  }`}
                >
                  {index < currentStage ? (
                    <Check className="w-5 h-5" />
                  ) : index === currentStage ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <span>{index + 1}</span>
                  )}
                </div>
                <span className="text-xs font-semibold">{stage.label}</span>
              </div>
            ))}
          </div>

          {/* Progress Bar */}
          <div className="w-full bg-gray-200 rounded-full h-2 mb-2">
            <div
              className="bg-primary h-2 rounded-full transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-sm text-gray-600 text-center">
            {STAGES[currentStage]?.description || 'Processing...'}
          </p>
        </div>

        {/* Status Messages */}
        {status === 'failed' && error && (
          <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-red-900">Translation Failed</p>
              <p className="text-sm text-red-700">{error}</p>
            </div>
          </div>
        )}

        {status === 'completed' && (
          <div className="p-4 bg-green-50 border border-green-200 rounded-lg flex items-center gap-3">
            <Check className="w-5 h-5 text-green-600 flex-shrink-0" />
            <p className="text-sm text-green-700 font-semibold">
              Translation completed successfully!
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
```

---

### Phase 3: Advanced Features (Week 3-4)  *(All sections below: Planned — Not Yet Implemented)*

#### 3.1 OCR for Scanned Documents *(Planned)*

> **Reality:** No OCR code is wired in. The pipeline's SCANNED branch ([backend/pipeline_service.py:262-279](backend/pipeline_service.py)) returns a placeholder block. The snippet below is **a design sample** showing one possible Tesseract integration. The model strategy now favours PaddleOCR over Tesseract — see the standalone model strategy audit; this section will be revisited when OCR work begins.

**File:** `backend/ocr_service.py` (new file — does not exist yet)

```python
import pytesseract
from PIL import Image
import fitz
from typing import List, Dict, Any

class OCRService:
    """OCR service for scanned documents"""
    
    def __init__(self):
        # Configure Tesseract for multiple languages
        self.tesseract_config = '--oem 3 --psm 6'
        self.supported_languages = 'eng+fil+ceb'  # English, Filipino, Cebuano
    
    def process_scanned_pdf(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Process scanned PDF with OCR
        
        Returns:
            List of pages with OCR text and coordinates
        """
        pages = []
        doc = fitz.open(pdf_path)
        
        for page_num in range(doc.page_count):
            page = doc[page_num]
            
            # Convert page to image
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # Run OCR with bounding boxes
            ocr_data = pytesseract.image_to_data(
                img,
                lang=self.supported_languages,
                config=self.tesseract_config,
                output_type=pytesseract.Output.DICT
            )
            
            # Extract text blocks with coordinates
            page_blocks = self._extract_blocks_from_ocr(ocr_data, page.rect.width, page.rect.height)
            
            pages.append({
                'page': page_num,
                'width': page.rect.width,
                'height': page.rect.height,
                'blocks': page_blocks,
                'ocr_confidence': self._calculate_avg_confidence(ocr_data)
            })
        
        doc.close()
        return pages
    
    def _extract_blocks_from_ocr(self, ocr_data: dict, page_width: float, page_height: float) -> List[Dict]:
        """Convert Tesseract OCR data to block format"""
        blocks = []
        
        # Group words into lines and blocks
        current_block = None
        
        for i in range(len(ocr_data['text'])):
            if ocr_data['text'][i].strip():
                x = ocr_data['left'][i]
                y = ocr_data['top'][i]
                w = ocr_data['width'][i]
                h = ocr_data['height'][i]
                text = ocr_data['text'][i]
                conf = ocr_data['conf'][i]
                
                # Create text block
                if current_block is None or abs(y - current_block['bbox'][1]) > 20:
                    if current_block:
                        blocks.append(current_block)
                    
                    current_block = {
                        'type': 'text',
                        'bbox': [x, y, x + w, y + h],
                        'lines': [{
                            'text': text,
                            'bbox': [x, y, x + w, y + h],
                            'confidence': conf
                        }]
                    }
                else:
                    # Add to existing block
                    current_block['lines'][-1]['text'] += ' ' + text
                    current_block['bbox'][2] = max(current_block['bbox'][2], x + w)
                    current_block['bbox'][3] = max(current_block['bbox'][3], y + h)
        
        if current_block:
            blocks.append(current_block)
        
        return blocks
    
    def _calculate_avg_confidence(self, ocr_data: dict) -> float:
        """Calculate average OCR confidence"""
        confidences = [conf for conf in ocr_data['conf'] if conf != -1]
        return sum(confidences) / len(confidences) if confidences else 0.0
```

#### 3.2 Language Detection Per Block *(Planned)*

> **Reality:** [backend/language_detection_service.py](backend/language_detection_service.py) **does exist** and supports document-level detection (langdetect + Tagabawa dictionary). What is **not yet implemented** is *per-block* detection wired into the pipeline output. Today the pipeline detects only the document's primary language. The snippet below is the per-block extension that has not been built.

**File:** `backend/language_detection_service.py` (per-block API to be added)

```python
from langdetect import detect, detect_langs
from typing import Dict, List

class LanguageDetectionService:
    """Detect language for text blocks"""
    
    LANGUAGE_MAP = {
        'en': 'english',
        'tl': 'filipino',
        'ceb': 'cebuano',
        'fil': 'filipino'
    }
    
    def detect_block_language(self, text: str) -> Dict[str, any]:
        """
        Detect language of a text block
        
        Returns:
            {
                'language': 'english' | 'filipino' | 'cebuano' | 'mixed',
                'confidence': float,
                'alternatives': List[Dict]
            }
        """
        try:
            # Get language probabilities
            lang_probs = detect_langs(text)
            
            primary_lang = lang_probs[0]
            mapped_lang = self.LANGUAGE_MAP.get(primary_lang.lang, 'unknown')
            
            return {
                'language': mapped_lang,
                'confidence': primary_lang.prob,
                'alternatives': [
                    {
                        'language': self.LANGUAGE_MAP.get(lp.lang, lp.lang),
                        'confidence': lp.prob
                    }
                    for lp in lang_probs[1:3]
                ]
            }
        except:
            return {
                'language': 'unknown',
                'confidence': 0.0,
                'alternatives': []
            }
    
    def detect_mixed_language(self, text: str) -> bool:
        """Check if text contains mixed languages"""
        try:
            lang_probs = detect_langs(text)
            # If top 2 languages have similar confidence, it's mixed
            if len(lang_probs) >= 2:
                return abs(lang_probs[0].prob - lang_probs[1].prob) < 0.3
            return False
        except:
            return False
```

#### 3.3 Uncertainty Flagging System *(Planned)*

> **Reality:** No `uncertainty_service.py` exists. The current translation cascade ([backend/translation_dataset.py](backend/translation_dataset.py)) returns either a translation or the original text — it does **not** emit a `[UNKNOWN]` marker, a confidence score, or a method tag. Adding this is part of the work blocking the bilingual UI.

**File:** `backend/uncertainty_service.py` (new file — does not exist yet)

```python
from typing import Dict, List
from dataclasses import dataclass

@dataclass
class UncertaintyFlag:
    """Represents an uncertain translation"""
    text: str
    translation: str
    reason: str  # 'no_match', 'low_confidence', 'mixed_language', 'special_term'
    confidence: float
    suggestions: List[str]

class UncertaintyService:
    """Flag uncertain translations for manual review"""
    
    def __init__(self):
        self.confidence_threshold = 0.7
        self.special_terms = self._load_special_terms()
    
    def flag_uncertain_translation(
        self,
        original: str,
        translation_result: 'TranslationResult'
    ) -> UncertaintyFlag:
        """
        Determine if translation needs manual review
        
        Criteria for flagging:
        - Confidence < 0.7
        - No translation found
        - Special terminology detected
        - Mixed language detected
        """
        flags = []
        
        # Check confidence
        if translation_result.confidence < self.confidence_threshold:
            flags.append(f"Low confidence: {translation_result.confidence:.2f}")
        
        # Check for [UNKNOWN] marker
        if '[UNKNOWN' in translation_result.translated:
            flags.append("No translation found")
        
        # Check for special terms
        if self._contains_special_term(original):
            flags.append("Contains special terminology")
        
        # Check translation method
        if translation_result.method == 'fuzzy':
            flags.append("Fuzzy match used")
        
        return UncertaintyFlag(
            text=original,
            translation=translation_result.translated,
            reason='; '.join(flags) if flags else 'confident',
            confidence=translation_result.confidence,
            suggestions=self._get_suggestions(original)
        )
    
    def _contains_special_term(self, text: str) -> bool:
        """Check if text contains special terminology"""
        text_lower = text.lower()
        return any(term in text_lower for term in self.special_terms)
    
    def _load_special_terms(self) -> List[str]:
        """Load list of special terms that need careful translation"""
        return [
            'bagobo', 'tagabawa', 'manobo', 'lumad',
            'datu', 'timuay', 'apo', 'cultural',
            # Add more special terms
        ]
    
    def _get_suggestions(self, text: str) -> List[str]:
        """Get alternative translation suggestions"""
        # This could integrate with the translation dataset
        # to find similar phrases
        return []
```

---

## Updated Pipeline Service Integration *(Planned target shape)*

> **Reality:** This is the **target** pipeline shape. The current [backend/pipeline_service.py](backend/pipeline_service.py) follows a similar 6-phase flow (detection → extraction → language detection → translation → reconstruction → preview → bilingual PDF) but does **not** call `self.ocr_service`, `self.uncertainty_service`, or per-block language detection — those services do not exist yet.

**File:** `backend/pipeline_service.py` (updated — design target)

```python
async def process_translation(
    self,
    job_id: str,
    input_file_path: str,
    file_type: FileType,
    source_language: str = "english",
    target_language: str = "tagabawa"
) -> bool:
    """Enhanced translation pipeline with all features"""
    
    job = JobStatus(job_id)
    job.file_type = file_type
    self.jobs[job_id] = job
    
    try:
        # Phase 1: Detection (10%)
        job.status = "processing"
        job.progress = 10
        job.metadata['stage'] = 'detection'
        
        detection_type = self.detection_service.detect_pdf_type(input_file_path)
        job.detection_type = detection_type
        
        # Phase 2: Extraction (25%)
        job.progress = 25
        job.metadata['stage'] = 'parsing'
        
        if detection_type == DetectionType.DIGITAL:
            layout_data = self.extraction_service.extract_pdf_with_layout_preservation(input_file_path)
        else:
            layout_data = self.ocr_service.process_scanned_pdf(input_file_path)
        
        job.metadata['layout_blocks'] = sum(len(page['blocks']) for page in layout_data)
        
        # Phase 3: Language Detection (40%)
        job.progress = 40
        job.metadata['stage'] = 'ocr'  # Using OCR stage for language detection
        
        for page in layout_data:
            for block in page['blocks']:
                if block['type'] == 'text':
                    for line in block['lines']:
                        lang_result = self.language_service.detect_block_language(line['text'])
                        line['detected_language'] = lang_result['language']
                        line['language_confidence'] = lang_result['confidence']
        
        # Phase 4: Translation (65%)
        job.progress = 50
        job.metadata['stage'] = 'translation'
        
        translations = {}
        uncertain_blocks = []
        
        for page in layout_data:
            for block in page['blocks']:
                if block['type'] == 'text':
                    for line in block['lines']:
                        original = line['text']
                        
                        # Translate with confidence
                        translation_result = self.translation_dataset.translate_with_confidence(
                            original,
                            source_lang=source_language,
                            target_lang=target_language
                        )
                        
                        translations[original] = translation_result
                        
                        # Flag uncertain translations
                        uncertainty_flag = self.uncertainty_service.flag_uncertain_translation(
                            original,
                            translation_result
                        )
                        
                        if uncertainty_flag.confidence < 0.7:
                            uncertain_blocks.append(uncertainty_flag)
        
        job.metadata['translated_blocks'] = len(translations)
        job.metadata['uncertain_blocks'] = len(uncertain_blocks)
        
        # Phase 5: PDF Reconstruction (85%)
        job.progress = 75
        job.metadata['stage'] = 'layout'
        
        output_pdf_path = self.file_service.get_output_path(job_id, "translated.pdf")
        
        success = self.reconstruction_service.reconstruct_pdf_with_layout(
            input_file_path,
            layout_data,
            translations,
            output_pdf_path
        )
        
        if not success:
            raise Exception("PDF reconstruction failed")
        
        # Phase 6: Preview Generation (95%)
        job.progress = 85
        
        preview_dir = os.path.join(self.file_service.get_job_dir(job_id), "preview")
        original_previews = self.reconstruction_service.create_preview_images(
            input_file_path, preview_dir, max_pages=5
        )
        translated_previews = self.reconstruction_service.create_preview_images(
            output_pdf_path, preview_dir, max_pages=5
        )
        
        job.metadata["preview_original"] = original_previews
        job.metadata["preview_translated"] = translated_previews
        
        # Phase 7: Completion (100%)
        job.status = "completed"
        job.progress = 100
        job.completed_at = datetime.now()
        job.metadata['stage'] = 'completed'
        
        return True
        
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.metadata['stage'] = 'failed'
        return False
```

---

## Dependencies — Current vs Target

**Current `backend/requirements.txt`:**

```text
fastapi[standard]>=0.128.1
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
pymupdf>=1.24.8
python-docx>=0.8.11
pillow>=10.4.0
numpy>=1.26.0
pydantic>=2.0.0
aiofiles>=23.2.1
reportlab>=4.2.0
pytesseract>=0.3.13       # ⚠ declared but not imported anywhere
rapidfuzz>=3.9.0          # used by translation_dataset.py
langdetect>=1.0.9
openpyxl>=3.1.0
```

> Note: the project chose **rapidfuzz**, not `fuzzywuzzy`/`python-Levenshtein`. Earlier drafts of this guide listed `fuzzywuzzy`; ignore that — `rapidfuzz` is what is actually wired in.

**Additional packages required by Planned phases (not yet added):**

```text
# OCR — pick one when Phase 3.1 is built
paddleocr>=2.7   # preferred per the model strategy audit
# (Tesseract path: keep pytesseract + system-installed Tesseract)

# Pretrained translation models — Planned (do NOT install until OCR + JSON contract are stable)
transformers>=4.40
sentencepiece
accelerate
# torch is platform-specific; install per pytorch.org instructions
```

---

## Testing Checklist

### Unit Tests

- [ ] PDF extraction preserves coordinates
- [ ] Translation fuzzy matching works correctly
- [ ] Layout reconstruction maintains positions
- [ ] OCR extracts text with coordinates
- [ ] Language detection identifies correct language
- [ ] Uncertainty flagging marks low-confidence translations

### Integration Tests

- [ ] Full pipeline processes digital PDF correctly
- [ ] Full pipeline processes scanned PDF with OCR
- [ ] Preview images are generated
- [ ] Side-by-side viewer displays correctly
- [ ] Progress modal updates in real-time
- [ ] Download delivers correct translated PDF

### End-to-End Tests

- [ ] Upload → Translate → Preview → Download workflow
- [ ] Layout preservation for logos, images, lines
- [ ] Text placement in original coordinates
- [ ] Font size adjustment for longer translations
- [ ] Multi-page documents handled correctly

---

## Deployment Notes

### Production Considerations

1. **Asynchronous Job Processing**
   - Current: Uses `asyncio.create_task` (not persistent)
   - Production: Use Celery, RQ, or similar task queue
   - Reason: Jobs survive server restarts

2. **File Storage**
   - Current: Local filesystem
   - Production: S3, Google Cloud Storage, or Azure Blob
   - Reason: Scalability and durability

3. **Database for Job Tracking**
   - Current: In-memory dictionary
   - Production: PostgreSQL or MongoDB
   - Reason: Persistent job history

4. **Rate Limiting**
   - Add rate limiting to prevent abuse
   - Consider per-user upload limits

5. **Preview Image Caching**
   - Cache preview images with CDN
   - Set appropriate cache headers

---

## Summary

This implementation guide provides a complete roadmap for building the layout-preserving PDF translation system for LingoKatutubo. The key innovations are:

1. **Layout Preservation**: Non-text elements stay in exact positions
2. **Intelligent Translation**: Multi-strategy approach with confidence scoring
3. **User-Friendly UI**: Side-by-side preview and progress tracking
4. **Quality Control**: Uncertainty flagging for manual review

Follow the phased approach to build incrementally while maintaining a working system at each stage.
