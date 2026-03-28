from __future__ import annotations

import json
from pathlib import Path


class ExtractionService:
    """Extracts text when feasible and falls back to metadata-only modes."""

    TEXT_EXTENSIONS = {
        ".txt", ".md", ".rst", ".json", ".yaml", ".yml", ".xml", ".csv",
        ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".cs", ".php",
    }
    OFFICE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}

    def extract(self, *, storage_path: str, filename: str, media_type: str) -> dict:
        path = Path(storage_path)
        suffix = path.suffix.lower() or Path(filename).suffix.lower()
        metadata = {
            "filename": filename,
            "media_type": media_type,
            "storage_path": storage_path,
            "suffix": suffix,
        }

        if suffix in self.TEXT_EXTENSIONS or media_type.startswith("text/") or media_type in {"application/json", "application/xml"}:
            text_content = path.read_text(encoding="utf-8", errors="ignore")
            mode = "text-extracted"
            if suffix == ".json":
                try:
                    parsed = json.loads(text_content)
                    metadata["json_root_type"] = type(parsed).__name__
                except Exception:
                    metadata["json_root_type"] = "invalid"
            return {
                "extraction_status": "completed",
                "extraction_mode": mode,
                "text_content": text_content,
                "metadata": metadata,
            }

        if suffix in self.OFFICE_EXTENSIONS:
            return {
                "extraction_status": "completed",
                "extraction_mode": "metadata-only",
                "text_content": None,
                "metadata": {**metadata, "reason": "office_extraction_not_enabled"},
            }

        return {
            "extraction_status": "completed",
            "extraction_mode": "raw-only",
            "text_content": None,
            "metadata": {**metadata, "reason": "binary_or_unsupported_format"},
        }


extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    return extraction_service
