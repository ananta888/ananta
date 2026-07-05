from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "integrations" / "open_notebook_export.v1.json"

SOURCE_SYSTEM = "open_notebook"

_WHITESPACE = re.compile(r"\s+")
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def validate_export_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def normalize_text(value: str) -> str:
    return _WHITESPACE.sub(" ", str(value or "")).strip()


def slugify(value: str, *, fallback: str = "open-notebook") -> str:
    slug = _SLUG_PATTERN.sub("-", str(value or "").strip().lower()).strip("-")
    return slug or fallback


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def source_content_hash(source: dict[str, Any]) -> str:
    full_text = normalize_text(str(source.get("full_text") or ""))
    if full_text:
        return _sha256_text(full_text)
    asset = dict(source.get("asset") or {})
    reference = str(asset.get("url") or asset.get("file_path") or "")
    return _sha256_text(f"asset:{reference}")


def build_import_key(payload: dict[str, Any]) -> str:
    """Deterministic identity for one external notebook/source set.

    Content hashes intentionally do not participate: changed source content is
    a new snapshot of the same imported source set, not a new registry source.
    """
    notebooks = sorted(str(item.get("id") or "") for item in list(payload.get("notebooks") or []))
    sources = sorted(str(item.get("id") or "") for item in list(payload.get("sources") or []) if isinstance(item, dict))
    basis = json.dumps({"notebooks": notebooks, "sources": sources}, sort_keys=True, separators=(",", ":"))
    return _sha256_text(basis)


class OpenNotebookMapper:
    """Maps a validated OpenNotebook export to Ananta-internal plans without persistence side effects."""

    def map_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        errors = validate_export_payload(payload)
        if errors:
            raise ValueError(f"invalid_open_notebook_export:{'; '.join(errors[:5])}")

        import_key = build_import_key(payload)
        notebooks = [item for item in list(payload.get("notebooks") or []) if isinstance(item, dict)]
        notebook_names = {str(item.get("id") or ""): str(item.get("name") or "") for item in notebooks}

        collection_plans: list[dict[str, Any]] = []
        for notebook in notebooks:
            external_id = str(notebook.get("id") or "").strip()
            collection_plans.append(
                {
                    "external_id": external_id,
                    "name": str(notebook.get("name") or external_id),
                    "description": str(notebook.get("description") or ""),
                    "metadata": {
                        "source_system": SOURCE_SYSTEM,
                        "open_notebook": {"notebook_id": external_id, "import_key": import_key},
                    },
                }
            )

        artifact_plans: list[dict[str, Any]] = []
        seen_source_ids: set[str] = set()
        for source in list(payload.get("sources") or []):
            if not isinstance(source, dict):
                continue
            source_id = str(source.get("id") or "").strip()
            if source_id in seen_source_ids:
                raise ValueError(f"duplicate_source_id:{source_id}")
            seen_source_ids.add(source_id)
            artifact_plans.append(self._map_source(source, import_key=import_key, notebook_names=notebook_names))

        return {
            "schema": "open_notebook_import_plan.v1",
            "source_system": SOURCE_SYSTEM,
            "import_key": import_key,
            "export_version": str(payload.get("export_version") or ""),
            "exported_at": str(payload.get("exported_at") or ""),
            "collections": collection_plans,
            "artifacts": artifact_plans,
            "notes": [item for item in list(payload.get("notes") or []) if isinstance(item, dict)],
            "source_insights": [item for item in list(payload.get("source_insights") or []) if isinstance(item, dict)],
            "transformations": [item for item in list(payload.get("transformations") or []) if isinstance(item, dict)],
            "chat_sessions": [item for item in list(payload.get("chat_sessions") or []) if isinstance(item, dict)],
        }

    def _map_source(
        self,
        source: dict[str, Any],
        *,
        import_key: str,
        notebook_names: dict[str, str],
    ) -> dict[str, Any]:
        source_id = str(source.get("id") or "").strip()
        title = str(source.get("title") or source_id)
        asset = dict(source.get("asset") or {})
        url = str(asset.get("url") or "") or None
        file_path = str(asset.get("file_path") or "") or None
        full_text = str(source.get("full_text") or "").strip()
        notebook_ids = [str(item) for item in list(source.get("notebook_ids") or []) if str(item).strip()]

        if full_text:
            content = full_text
            media_type = "text/markdown"
            filename = f"{slugify(title, fallback=slugify(source_id))}.md"
        else:
            reference = url or file_path or ""
            content = f"# {title}\n\nExternal OpenNotebook asset reference: {reference}\n"
            media_type = "text/markdown"
            filename = f"{slugify(title, fallback=slugify(source_id))}.reference.md"

        return {
            "external_id": source_id,
            "title": title,
            "filename": filename,
            "media_type": media_type,
            "content": content,
            "url": url,
            "file_path": file_path,
            "topics": [str(item) for item in list(source.get("topics") or [])],
            "notebook_ids": notebook_ids,
            "collection_names": [notebook_names.get(item) or item for item in notebook_ids],
            "content_hash": source_content_hash(source),
            "has_inline_text": bool(full_text),
            "created": str(source.get("created") or ""),
            "updated": str(source.get("updated") or ""),
            "metadata": {
                "source_system": SOURCE_SYSTEM,
                "open_notebook": {
                    "source_id": source_id,
                    "notebook_ids": notebook_ids,
                    "import_key": import_key,
                },
                **dict(source.get("metadata") or {}),
            },
        }
