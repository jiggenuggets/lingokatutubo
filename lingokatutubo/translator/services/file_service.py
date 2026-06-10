"""
File service for handling uploaded documents
"""

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from models import FileType


class FileService:
    """Manages file uploads, working job storage, and cleanup."""
    
    def __init__(
        self,
        upload_dir: Optional[str] = None,
        jobs_dir: Optional[str] = None,
        storage_layout: Optional[str] = None,
    ):
        """
        Initialize file service
        
        Args:
            upload_dir: Storage root. Defaults to /tmp/bagobo-uploads/.
            jobs_dir: Optional explicit jobs root.
            storage_layout: "legacy" keeps the old flat job directories;
                "django" stores uploads and jobs under media-style folders.
        """
        self.storage_layout = (
            storage_layout
            or os.environ.get("LINGOKATUTUBO_STORAGE_LAYOUT")
            or "legacy"
        ).strip().lower()

        default_root = os.path.join(tempfile.gettempdir(), "bagobo-uploads")
        self.upload_dir = str(
            upload_dir
            or os.environ.get("LINGOKATUTUBO_STORAGE_ROOT")
            or os.environ.get("LINGOKATUTUBO_UPLOAD_DIR")
            or default_root
        )
        self.jobs_dir = str(jobs_dir or os.path.join(self.upload_dir, "jobs"))
        self.uploads_dir = (
            os.path.join(self.upload_dir, "uploads")
            if self.storage_layout == "django"
            else self.upload_dir
        )
        
        # Create directories
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.uploads_dir, exist_ok=True)
        os.makedirs(self.jobs_dir, exist_ok=True)
    
    async def save_upload(self, file_content: bytes, original_filename: str, job_id: str) -> str:
        """
        Save uploaded file with job ID
        
        Args:
            file_content: The file bytes
            original_filename: Original filename from upload
            job_id: Unique job identifier
        
        Returns:
            Path to saved file
        """
        # Sanitize filename and save
        safe_name = self._safe_filename(original_filename)

        if self.storage_layout == "django":
            upload_path = self.get_upload_archive_path(job_id, original_filename)
            os.makedirs(os.path.dirname(upload_path), exist_ok=True)
            with open(upload_path, "wb") as f:
                f.write(file_content)

            input_dir = self.get_input_dir(job_id)
            file_path = os.path.join(input_dir, safe_name)
            shutil.copyfile(upload_path, file_path)
            return file_path

        job_dir = self.get_job_dir(job_id)
        os.makedirs(job_dir, exist_ok=True)
        file_path = os.path.join(job_dir, f"input_{safe_name}")

        with open(file_path, "wb") as f:
            f.write(file_content)

        return file_path

    @staticmethod
    def _safe_filename(original_filename: str) -> str:
        path = Path(original_filename or "document")
        safe_name = f"{path.stem}{path.suffix}"
        return safe_name or "document"

    def get_upload_archive_path(self, job_id: str, original_filename: str) -> str:
        """Get the archived upload path under media/uploads/{job_id}/."""
        if self.storage_layout != "django":
            return os.path.join(self.get_job_dir(job_id), f"input_{self._safe_filename(original_filename)}")
        upload_dir = os.path.join(self.uploads_dir, job_id)
        os.makedirs(upload_dir, exist_ok=True)
        return os.path.join(upload_dir, self._safe_filename(original_filename))

    def get_input_dir(self, job_id: str) -> str:
        """Get the input working directory for a job."""
        job_dir = self.get_job_dir(job_id)
        input_dir = os.path.join(job_dir, "input") if self.storage_layout == "django" else job_dir
        os.makedirs(input_dir, exist_ok=True)
        return input_dir
    
    def get_job_dir(self, job_id: str) -> str:
        """Get the working directory for a job"""
        return os.path.join(self.jobs_dir, job_id)
    
    def get_output_path(self, job_id: str, filename: str) -> str:
        """Get path for output file in job directory"""
        job_dir = self.get_job_dir(job_id)
        output_dir = (
            os.path.join(job_dir, "translated")
            if self.storage_layout == "django"
            else job_dir
        )
        os.makedirs(output_dir, exist_ok=True)
        return os.path.join(output_dir, filename)
    
    def _resolve_cleanup_job_dir(self, job_id: str) -> str:
        """Resolve a job directory and prove it stays inside the jobs root."""
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("Unsafe job_id")

        normalized_job_id = job_id.strip()
        if (
            normalized_job_id in {".", ".."}
            or os.sep in normalized_job_id
            or (os.altsep and os.altsep in normalized_job_id)
            or ":" in normalized_job_id
        ):
            raise ValueError("Unsafe job_id")

        jobs_root = os.path.abspath(self.jobs_dir)
        job_dir = os.path.abspath(os.path.join(jobs_root, normalized_job_id))

        jobs_root_cmp = os.path.normcase(jobs_root)
        job_dir_cmp = os.path.normcase(job_dir)
        try:
            common_path = os.path.commonpath([jobs_root_cmp, job_dir_cmp])
        except ValueError as exc:
            raise ValueError("Unsafe job path") from exc
        if common_path != jobs_root_cmp or job_dir_cmp == jobs_root_cmp:
            raise ValueError("Unsafe job path")

        jobs_root_real = os.path.normcase(os.path.realpath(jobs_root))
        job_dir_real = os.path.normcase(os.path.realpath(job_dir))
        try:
            real_common_path = os.path.commonpath([jobs_root_real, job_dir_real])
        except ValueError as exc:
            raise ValueError("Unsafe job path") from exc
        if real_common_path != jobs_root_real or job_dir_real == jobs_root_real:
            raise ValueError("Unsafe job path")

        return job_dir

    def _active_job_ids(self, active_job_ids: Optional[Iterable[str]]) -> set:
        if not active_job_ids:
            return set()
        return {str(job_id) for job_id in active_job_ids}

    def cleanup_job(
        self,
        job_id: str,
        active_job_ids: Optional[Iterable[str]] = None,
    ) -> bool:
        """Remove all files for one inactive job, returning True if deleted."""
        if job_id in self._active_job_ids(active_job_ids):
            return False

        job_dir = self._resolve_cleanup_job_dir(job_id)
        if not os.path.isdir(job_dir):
            return False

        shutil.rmtree(job_dir)
        if self.storage_layout == "django":
            upload_dir = self._resolve_cleanup_upload_dir(job_id)
            if os.path.isdir(upload_dir):
                shutil.rmtree(upload_dir)
        return True

    def cleanup_old_jobs(
        self,
        max_age_seconds: float,
        active_job_ids: Optional[Iterable[str]] = None,
        now: Optional[float] = None,
    ) -> Dict[str, List[str]]:
        """
        Remove inactive job directories older than max_age_seconds.

        Only direct children of the jobs root are considered. Unsafe names are
        skipped and reported instead of being deleted.
        """
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")

        active_ids = self._active_job_ids(active_job_ids)
        cutoff_now = time.time() if now is None else now
        result: Dict[str, List[str]] = {
            "removed": [],
            "skipped_active": [],
            "skipped_recent": [],
            "unsafe": [],
            "errors": [],
        }

        if not os.path.isdir(self.jobs_dir):
            return result

        with os.scandir(self.jobs_dir) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue

                job_id = entry.name
                if job_id in active_ids:
                    result["skipped_active"].append(job_id)
                    continue

                try:
                    job_dir = self._resolve_cleanup_job_dir(job_id)
                    stat = entry.stat(follow_symlinks=False)
                except ValueError:
                    result["unsafe"].append(job_id)
                    continue
                except OSError as exc:
                    result["errors"].append(f"{job_id}: {exc}")
                    continue

                age_seconds = cutoff_now - stat.st_mtime
                if age_seconds < max_age_seconds:
                    result["skipped_recent"].append(job_id)
                    continue

                try:
                    shutil.rmtree(job_dir)
                    if self.storage_layout == "django":
                        upload_dir = self._resolve_cleanup_upload_dir(job_id)
                        if os.path.isdir(upload_dir):
                            shutil.rmtree(upload_dir)
                    result["removed"].append(job_id)
                except OSError as exc:
                    result["errors"].append(f"{job_id}: {exc}")

        return result

    def _resolve_cleanup_upload_dir(self, job_id: str) -> str:
        """Resolve an archived upload directory and prove it stays inside uploads."""
        if self.storage_layout != "django":
            return self._resolve_cleanup_job_dir(job_id)

        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("Unsafe job_id")

        normalized_job_id = job_id.strip()
        if (
            normalized_job_id in {".", ".."}
            or os.sep in normalized_job_id
            or (os.altsep and os.altsep in normalized_job_id)
            or ":" in normalized_job_id
        ):
            raise ValueError("Unsafe job_id")

        uploads_root = os.path.abspath(self.uploads_dir)
        upload_dir = os.path.abspath(os.path.join(uploads_root, normalized_job_id))
        common_path = os.path.commonpath([
            os.path.normcase(uploads_root),
            os.path.normcase(upload_dir),
        ])
        if common_path != os.path.normcase(uploads_root) or os.path.normcase(upload_dir) == os.path.normcase(uploads_root):
            raise ValueError("Unsafe upload path")
        return upload_dir
    
    @staticmethod
    def get_file_type(filename: str) -> Optional[FileType]:
        """Detect file type from extension"""
        ext = Path(filename).suffix.lower()
        ext_map = {
            '.pdf': FileType.PDF,
            '.docx': FileType.DOCX,
            '.jpg': FileType.JPG,
            '.jpeg': FileType.JPG,
            '.png': FileType.PNG,
        }
        return ext_map.get(ext)
    
    def file_exists(self, file_path: str) -> bool:
        """Check if file exists"""
        return os.path.exists(file_path) and os.path.isfile(file_path)
    
    def get_file_size(self, file_path: str) -> int:
        """Get file size in bytes"""
        if self.file_exists(file_path):
            return os.path.getsize(file_path)
        return 0


# Global instance
_file_service = None


def get_file_service() -> FileService:
    """Get or create the global file service instance"""
    global _file_service
    if _file_service is None:
        _file_service = FileService()
    return _file_service

