"""Record loading for completed knowledge indices.

Reads rag-helper output files, flattens records into retrieval text and
hydrates exact, hash-verified records from Hub-persisted selectors.

``KnowledgeIndexRecordReader`` is a stateless collaborator of
``KnowledgeIndexRetrievalService``; it owns no repository and needs no host.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol


class KnowledgeIndexRecordSource(Protocol):
    """What the retrieval service needs to read and describe index records."""

    OUTPUT_FILENAMES: tuple[str, ...]

    def iter_output_records(self, output_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]: ...

    def record_text(self, record: dict[str, Any]) -> str: ...

    def record_field_texts(self, record: dict[str, Any], source_hint: str) -> dict[str, str]: ...

    def display_path(self, record: dict[str, Any]) -> str: ...

    def load_bound_records(
        self,
        *,
        knowledge_index: Any,
        bindings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]: ...


class KnowledgeIndexRecordReader:
    """Reads and flattens index records; hydrates hash-bound records."""

    OUTPUT_FILENAMES = ("index.jsonl", "details.jsonl", "relations.jsonl")
    MAX_BOUND_RECORDS = 400
    MAX_BOUND_INDEX_FILE_BYTES = 512 * 1024 * 1024
    MAX_BOUND_RECORD_LINE_BYTES = 2 * 1024 * 1024
    FIELD_EXCLUDE_KEYS = {"id", "parent_id", "node_id", "edge_id", "hash", "sha1", "sha256"}

    def iter_output_records(self, output_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]:
        for filename in self.OUTPUT_FILENAMES:
            path = output_dir / filename
            if not path.exists():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        yield filename, payload
            except OSError:
                continue

    def _flatten_scalars(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, int, float, bool)):
            return [str(value)]
        if isinstance(value, dict):
            parts: list[str] = []
            for key, nested in value.items():
                if key in self.FIELD_EXCLUDE_KEYS:
                    continue
                parts.extend(self._flatten_scalars(nested))
            return parts
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                parts.extend(self._flatten_scalars(item))
            return parts
        return []

    def record_text(self, record: dict[str, Any]) -> str:
        preferred_parts: list[str] = []
        for key in ("title", "name", "content", "text", "path", "tag", "relation", "file", "kind"):
            if key in record:
                preferred_parts.extend(self._flatten_scalars(record.get(key)))
        if "summary" in record:
            preferred_parts.extend(self._flatten_scalars(record.get("summary")))
        if "symbols" in record:
            preferred_parts.extend(self._flatten_scalars(record.get("symbols")))
        all_parts = preferred_parts + self._flatten_scalars(record)
        compact = " ".join(part.strip() for part in all_parts if str(part).strip())
        return re.sub(r"\s+", " ", compact).strip()[:2000]

    def load_bound_records(
        self,
        *,
        knowledge_index: Any,
        bindings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Hydrate exact immutable index records without running a new search.

        The caller supplies only Hub-persisted record selectors.  Every record
        is resolved from the named completed index and its reconstructed
        retrieval text must match the cataloged SHA-256 before it is returned.
        """

        if (
            str(getattr(knowledge_index, "status", "") or "") != "completed"
            or not bindings
            or len(bindings) > self.MAX_BOUND_RECORDS
        ):
            raise ValueError("knowledge_index_bound_records_invalid")
        output = Path(str(getattr(knowledge_index, "output_dir", "") or ""))
        try:
            if output.is_symlink() or not output.is_dir():
                raise ValueError
            resolved_output = output.resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise ValueError("knowledge_index_bound_records_unavailable") from exc

        selectors: dict[tuple[str, str, str, int | None, int | None], dict[str, Any]] = {}
        for raw in bindings:
            binding = dict(raw or {})
            filename = str(binding.get("record_file") or "").strip()
            source_id = str(binding.get("source_id") or "").strip()
            expected_hash = str(binding.get("content_hash") or "").strip().lower()
            if (
                filename not in self.OUTPUT_FILENAMES
                or not source_id
                or not _is_sha256(expected_hash)
            ):
                raise ValueError("knowledge_index_bound_record_selector_invalid")
            key = (
                filename,
                str(binding.get("record_id") or "").strip(),
                str(binding.get("path") or "").strip(),
                self._bound_line(binding.get("line_start")),
                self._bound_line(binding.get("line_end")),
            )
            if key in selectors:
                raise ValueError("knowledge_index_bound_record_selector_duplicate")
            selectors[key] = binding

        matches: dict[tuple[str, str, str, int | None, int | None], dict[str, Any]] = {}
        for filename in sorted({key[0] for key in selectors}):
            path = resolved_output / filename
            try:
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_size > self.MAX_BOUND_INDEX_FILE_BYTES
                    or path.resolve(strict=True).parent != resolved_output
                ):
                    raise ValueError
            except (OSError, ValueError) as exc:
                raise ValueError("knowledge_index_bound_record_file_invalid") from exc
            try:
                with path.open("rb") as handle:
                    while True:
                        raw_line = handle.readline(self.MAX_BOUND_RECORD_LINE_BYTES + 1)
                        if not raw_line:
                            break
                        if len(raw_line) > self.MAX_BOUND_RECORD_LINE_BYTES:
                            raise ValueError("knowledge_index_bound_record_too_large")
                        if not raw_line.strip():
                            continue
                        try:
                            payload = json.loads(raw_line.decode("utf-8"))
                        except (UnicodeError, json.JSONDecodeError) as exc:
                            raise ValueError(
                                "knowledge_index_bound_record_json_invalid"
                            ) from exc
                        if not isinstance(payload, dict):
                            continue
                        key = (
                            filename,
                            str(payload.get("id") or "").strip(),
                            str(payload.get("path") or payload.get("file") or "").strip(),
                            self._bound_line(
                                payload.get("line_start", payload.get("start_line"))
                            ),
                            self._bound_line(
                                payload.get("line_end", payload.get("end_line"))
                            ),
                        )
                        binding = selectors.get(key)
                        if binding is None:
                            continue
                        if key in matches:
                            raise ValueError("knowledge_index_bound_record_ambiguous")
                        content = self.record_text(payload)
                        content_hash = hashlib.sha256(
                            content.encode("utf-8", errors="strict")
                        ).hexdigest()
                        if content_hash != str(binding["content_hash"]).lower():
                            raise ValueError("knowledge_index_bound_record_content_mismatch")
                        matches[key] = {
                            **binding,
                            "content": content,
                        }
            except OSError as exc:
                raise ValueError("knowledge_index_bound_record_file_unavailable") from exc

        missing = set(selectors) - set(matches)
        if missing:
            raise ValueError("knowledge_index_bound_record_not_found")
        return [
            matches[key]
            for key in sorted(
                matches,
                key=lambda value: str(selectors[value].get("source_id") or ""),
            )
        ]

    @staticmethod
    def _bound_line(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("knowledge_index_bound_record_line_invalid")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("knowledge_index_bound_record_line_invalid") from exc
        if normalized < 1:
            raise ValueError("knowledge_index_bound_record_line_invalid")
        return normalized

    def record_field_texts(self, record: dict[str, Any], source_hint: str) -> dict[str, str]:
        def _join(keys: tuple[str, ...]) -> str:
            values: list[str] = []
            for key in keys:
                if key in record:
                    values.extend(self._flatten_scalars(record.get(key)))
            return " ".join(str(item).strip() for item in values if str(item).strip())

        return {
            "symbol": _join(("symbol", "symbols", "name", "title")),
            "kind": _join(("kind", "type", "tag")),
            "path": _join(("path", "file")) or source_hint,
            "relations": _join(("relation", "relations", "parents", "children", "depends_on", "references")),
            "summary": _join(("summary",)),
            "content": _join(("content", "text", "description", "snippet")),
            "focus": _join(
                (
                    "focus_terms",
                    "retrieval_focus",
                    "role_labels",
                    "framework_roles",
                    "member_names",
                    "member_kinds",
                    "chunk_granularity",
                    "domain",
                    "embedding_text",
                )
            ),
        }

    @staticmethod
    def display_path(record: dict[str, Any]) -> str:
        """Repository path of a record, wherever the producer put it."""
        for value in (
            record.get("file"),
            record.get("path"),
            nested_metadata_value(record, "relative_path"),
            nested_metadata_value(record, "path"),
            nested_metadata_value(record, "file"),
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""


def nested_metadata_value(record: dict[str, Any], key: str) -> Any:
    """Value of ``record["metadata"][key]`` when the record carries metadata."""
    metadata = record.get("metadata")
    return metadata.get(key) if isinstance(metadata, dict) else None


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)
