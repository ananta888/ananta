from __future__ import annotations

import json
from pathlib import Path


class ExtractionService:
    """Extracts text when feasible and falls back to metadata-only modes."""

    FULLY_INDEXED_EXTENSIONS = {
        ".md", ".rst", ".json", ".yaml", ".yml", ".xml", ".csv",
        ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".cs", ".php",
    }
    TEXT_EXTRACTED_EXTENSIONS = {".txt", ".log", ".ini", ".toml"}
    OFFICE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
    BINARY_REFERENCE_EXTENSIONS = {
        ".png", ".jpg", ".jpeg", ".webp",
        ".mp3", ".wav", ".mp4", ".mkv",
        ".zip", ".tar", ".gz", ".eml", ".msg",
    }

    def _base_metadata(self, *, storage_path: str, filename: str, media_type: str, suffix: str) -> dict:
        return {
            "filename": filename,
            "media_type": media_type,
            "storage_path": storage_path,
            "suffix": suffix,
        }

    def _read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore")

    def extract(self, *, storage_path: str, filename: str, media_type: str) -> dict:
        path = Path(storage_path)
        suffix = path.suffix.lower() or Path(filename).suffix.lower()
        metadata = self._base_metadata(storage_path=storage_path, filename=filename, media_type=media_type, suffix=suffix)

        if (
            suffix in self.FULLY_INDEXED_EXTENSIONS
            or media_type in {"application/json", "application/xml"}
            or media_type.startswith("application/x-python")
        ):
            text_content = self._read_text(path)
            mode = "fully-indexed"
            if suffix == ".json":
                try:
                    parsed = json.loads(text_content)
                    metadata["json_root_type"] = type(parsed).__name__
                except Exception:
                    metadata["json_root_type"] = "invalid"
            metadata["content_family"] = "structured_text"
            return {
                "extraction_status": "completed",
                "extraction_mode": mode,
                "text_content": text_content,
                "metadata": metadata,
            }

        if suffix in self.TEXT_EXTRACTED_EXTENSIONS or media_type.startswith("text/"):
            text_content = self._read_text(path)
            return {
                "extraction_status": "completed",
                "extraction_mode": "text-extracted",
                "text_content": text_content,
                "metadata": {**metadata, "content_family": "plain_text"},
            }

        if suffix in self.OFFICE_EXTENSIONS:
            return {
                "extraction_status": "completed",
                "extraction_mode": "metadata-only",
                "text_content": None,
                "metadata": {**metadata, "content_family": "office_document", "reason": "office_extraction_not_enabled"},
            }

        if suffix in self.BINARY_REFERENCE_EXTENSIONS:
            return {
                "extraction_status": "completed",
                "extraction_mode": "raw-only",
                "text_content": None,
                "metadata": {**metadata, "content_family": "binary_reference", "reason": "binary_or_reference_only"},
            }

        return {
            "extraction_status": "completed",
            "extraction_mode": "raw-only",
            "text_content": None,
            "metadata": {**metadata, "content_family": "unknown_binary", "reason": "binary_or_unsupported_format"},
        }


extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    return extraction_service
