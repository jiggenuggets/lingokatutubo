"""
Pydantic models for API requests and responses
"""

from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


class FileType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    JPG = "jpg"
    PNG = "png"
    TXT = "txt"


class DetectionType(str, Enum):
    DIGITAL = "digital"
    SCANNED = "scanned"


class TranslateRequest(BaseModel):
    """Request model for POST /translate"""
    file_type: FileType
    source_language: str = "english"
    target_language: str = "tagabawa"


class LayoutObject(BaseModel):
    """Represents a detected layout object in the document"""
    object_type: str  # "text", "image", "line", "table", "heading", "paragraph"
    bbox: dict  # {"x0": float, "top": float, "x1": float, "bottom": float}
    page: int
    content: Optional[str] = None
    is_translated: bool = False
    translated_content: Optional[str] = None


class TextSegment(BaseModel):
    """A segment of text with metadata"""
    content: str
    page: int
    bbox: dict
    font_info: Optional[dict] = None
    is_heading: bool = False
    is_list_item: bool = False


class TranslateResponse(BaseModel):
    """Response from POST /translate"""
    job_id: str
    status: str  # "processing", "completed", "failed"
    message: Optional[str] = None
    progress_percent: Optional[int] = None
    current_phase: Optional[str] = None
    current_step: Optional[str] = None
    phase_message: Optional[str] = None


class JobStatus(BaseModel):
    """Response from GET /status/{job_id}"""
    job_id: str
    status: str
    detection_type: Optional[DetectionType] = None
    file_type: Optional[FileType] = None
    progress_percent: int = 0
    current_phase: Optional[str] = None
    current_step: Optional[str] = None
    phase_message: Optional[str] = None
    error: Optional[str] = None


class PreviewData(BaseModel):
    """Preview data for bilingual view"""
    job_id: str
    original_pages: List[str]  # paths or base64
    translated_pages: List[str]  # paths or base64
    page_count: int
    source_language: str
    target_language: str


class DownloadResponse(BaseModel):
    """Response for GET /download/{job_id}"""
    job_id: str
    file_url: str
    file_name: str
    file_size_bytes: int

