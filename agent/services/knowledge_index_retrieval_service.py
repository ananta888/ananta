from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable

from agent.config import settings
from agent.hybrid_orchestrator import ContextChunk
from agent.repository import knowledge_index_repo, knowledge_link_repo
from agent.services.knowledge_index_consumption_policy import (
    KnowledgeIndexConsumptionPolicy,
    get_knowledge_index_consumption_policy,
)
from agent.services.retrieval_source_contract import normalize_chunk_metadata
from ananta_contracts.file_type_classifier import FileTypeClassifier
from ananta_contracts.file_type_support import (
    FileTypeSupportRegistry,
    load_file_type_support_registry,
)


class KnowledgeIndexRetrievalService:
    """Reads completed rag-helper outputs as an additive retrieval source."""

    OUTPUT_FILENAMES = ("index.jsonl", "details.jsonl", "relations.jsonl")
    MAX_BOUND_RECORDS = 400
    MAX_BOUND_INDEX_FILE_BYTES = 512 * 1024 * 1024
    MAX_BOUND_RECORD_LINE_BYTES = 2 * 1024 * 1024
    FIELD_EXCLUDE_KEYS = {"id", "parent_id", "node_id", "edge_id", "hash", "sha1", "sha256"}
    TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
    SYMBOL_SPLIT_PATTERN = re.compile(r"(?<!^)(?=[A-Z])")
    # Stop tokens filtered before scoring. Includes German articles/pronouns and
    # common 2-char fragments produced by splitting words at umlauts (e.g. "erkläre"
    # → ["erkl", "re"]). Without this filter, "re" dominates large Python files
    # through "return", "service", "regex" etc. hitting the linear count formula.
    _STOP_TOKENS: frozenset = frozenset({
        # German articles / pronouns / prepositions
        "der", "die", "das", "den", "dem", "des",
        "ein", "eine", "einer", "eines", "einem",
        "mir", "dir", "ihm", "ihr", "uns",
        "ich", "du", "er", "sie", "wir",
        "und", "oder", "aber", "nicht", "auch", "noch",
        "von", "mit", "bei", "aus", "zur", "zum",
        "ist", "sind", "war", "wird", "hat", "haben",
        "auf", "in", "an", "zu", "am", "im",
        "als", "bitte", "mal", "schon", "wie", "was", "wer", "wo",
        # Common short fragments from umlaut splitting
        "re", "de", "le", "al", "ar", "te", "se",
        # English stop words
        "the", "and", "for", "are", "but", "not", "you", "all",
        "can", "has", "its", "was", "use", "one", "how", "our", "out",
    })
    FILE_KIND_BY_FAMILY = {
        "build": "config",
        "code": "code",
        "configuration": "config",
        "data": "config",
        "diagram": "doc",
        "documentation": "doc",
        "fallback": "other",
        "notebook": "code",
        "script": "code",
        "style": "code",
        "template": "code",
    }
    CODE_KIND_MARKERS = (
        "class",
        "function",
        "method",
        "symbol",
        "type",
        "enum",
        "interface",
        "impl",
        "code",
    )
    DOC_KIND_MARKERS = (
        "md_",
        "doc",
        "readme",
        "adr",
        "guide",
        "architecture",
        "overview",
        "policy",
    )
    RELATION_KIND_MARKERS = ("relation", "edge", "link", "reference", "dependency", "call", "import")
    BROAD_SUMMARY_KIND_MARKERS = ("summary", "overview")

    def __init__(
        self,
        knowledge_index_repository=None,
        knowledge_link_repository=None,
        *,
        file_type_registry: FileTypeSupportRegistry | None = None,
        consumption_policy: KnowledgeIndexConsumptionPolicy | None = None,
    ) -> None:
        self._knowledge_index_repository = knowledge_index_repository or knowledge_index_repo
        self._knowledge_link_repository = knowledge_link_repository or knowledge_link_repo
        registry = file_type_registry or load_file_type_support_registry(
            Path(__file__).resolve().parents[2]
        )
        self._file_type_classifier = FileTypeClassifier(registry)
        self._consumption_policy = (
            consumption_policy or get_knowledge_index_consumption_policy()
        )

    def _collection_metadata(self, artifact_id: str) -> tuple[list[str], list[str]]:
        if not artifact_id:
            return [], []
        links = self._knowledge_link_repository.get_by_artifact(artifact_id)
        collection_ids: list[str] = []
        collection_names: list[str] = []
        for link in links:
            collection_id = str(getattr(link, "collection_id", "") or "").strip()
            collection_name = str(((getattr(link, "link_metadata", None) or {}).get("collection_name")) or "").strip()
            if collection_id and collection_id not in collection_ids:
                collection_ids.append(collection_id)
            if collection_name and collection_name not in collection_names:
                collection_names.append(collection_name)
        return collection_ids, collection_names

    def _iter_completed_indices(
        self,
        *,
        allowed_index_ids: set[str] | None = None,
    ):
        for knowledge_index in self._knowledge_index_repository.list_completed():
            if self._consumption_policy.can_consume(
                knowledge_index,
                allowed_index_ids=allowed_index_ids,
            ):
                yield knowledge_index

    def get_source_preflight(self) -> dict[str, object]:
        root = Path(settings.data_dir) / "knowledge_indices"
        by_scope: dict[str, dict[str, object]] = {
            "artifact": {
                "status": "degraded",
                "completed_indices": 0,
                "issues": [],
                "storage_root": str((root / "artifact").resolve()),
            },
            "wiki": {
                "status": "degraded",
                "completed_indices": 0,
                "issues": [],
                "storage_root": str((root / "wiki").resolve()),
            },
        }
        for knowledge_index in self._iter_completed_indices():
            scope = (
                str(getattr(knowledge_index, "source_scope", "artifact") or "artifact")
                .strip()
                .lower()
                or "artifact"
            )
            if scope not in by_scope:
                by_scope[scope] = {
                    "status": "degraded",
                    "completed_indices": 0,
                    "issues": [],
                    "storage_root": str((root / scope).resolve()),
                }
            bucket = by_scope[scope]
            bucket["completed_indices"] = int(bucket.get("completed_indices") or 0) + 1

            output_dir_raw = getattr(knowledge_index, "output_dir", None)
            if not output_dir_raw:
                issues = list(bucket.get("issues") or [])
                issues.append("missing_output_dir")
                bucket["issues"] = issues
                continue
            output_dir = Path(output_dir_raw)
            if not output_dir.exists() or not output_dir.is_dir():
                issues = list(bucket.get("issues") or [])
                issues.append("output_dir_missing")
                bucket["issues"] = issues
                continue
            if not any((output_dir / name).exists() for name in self.OUTPUT_FILENAMES):
                issues = list(bucket.get("issues") or [])
                issues.append("no_index_outputs")
                bucket["issues"] = issues

        for scope, bucket in by_scope.items():
            issues = list(bucket.get("issues") or [])
            completed = int(bucket.get("completed_indices") or 0)
            if completed <= 0:
                bucket["status"] = "degraded"
                issues.append("no_completed_indices")
            elif issues:
                bucket["status"] = "degraded"
            else:
                bucket["status"] = "ok"
            # de-duplicate while keeping order
            seen: set[str] = set()
            unique_issues: list[str] = []
            for issue in issues:
                if issue not in seen:
                    seen.add(issue)
                    unique_issues.append(issue)
            bucket["issues"] = unique_issues
            by_scope[scope] = bucket
        return by_scope

    def _iter_output_records(self, output_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]:
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

    def _record_text(self, record: dict[str, Any]) -> str:
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
                        content = self._record_text(payload)
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

    def _tokenize(self, value: str) -> list[str]:
        return [
            t for t in (token.lower() for token in self.TOKEN_PATTERN.findall(value or ""))
            if len(t) >= 3 and t not in self._STOP_TOKENS
        ]

    def _query_features(self, query: str) -> dict[str, list[str]]:
        tokens = self._tokenize(query)
        symbols: list[str] = []
        for token in tokens:
            has_symbol_shape = (
                "_" in token
                or any(char.isdigit() for char in token)
                or (len(token) >= 6 and any(char.isupper() for char in query))
            )
            if has_symbol_shape:
                symbols.append(token)
        for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query or ""):
            parts = [part.lower() for part in self.SYMBOL_SPLIT_PATTERN.split(raw) if part]
            if len(parts) > 1:
                symbols.extend(parts)
        unique_tokens = sorted(set(tokens))
        unique_symbols = sorted(set(symbols))
        return {"tokens": unique_tokens, "symbols": unique_symbols}

    def _task_profile(self, task_kind: str | None, retrieval_intent: str | None) -> dict[str, Any]:
        normalized_kind = str(task_kind or "").strip().lower()
        normalized_intent = str(retrieval_intent or "").strip().lower()
        profile = {
            "record_kind_weights": {"code": 1.0, "doc": 1.0, "relation": 1.0, "other": 1.0},
            "file_kind_weights": {"code": 1.0, "doc": 1.0, "config": 1.0},
            "symbol_multiplier": 1.0,
            "relation_bonus": 0.0,
            "importance_weight": 0.08,
            "duplicate_penalty": 0.12,
            "generated_penalty": 0.18,
            "boilerplate_penalty": 0.08,
        }
        if normalized_kind in {"bugfix", "implement", "coding", "refactor", "test", "testing"}:
            profile["record_kind_weights"]["code"] = 1.25
            profile["record_kind_weights"]["relation"] = 1.15
            profile["file_kind_weights"]["code"] = 1.2
            profile["symbol_multiplier"] = 1.15
            profile["relation_bonus"] = 0.35
            profile["importance_weight"] = 0.12
            profile["duplicate_penalty"] = 0.18
        if normalized_kind in {"architecture", "analysis", "doc", "research"}:
            profile["record_kind_weights"]["doc"] = 1.3
            profile["record_kind_weights"]["relation"] = 1.1
            profile["file_kind_weights"]["doc"] = 1.25
            profile["boilerplate_penalty"] = 0.14
        if normalized_kind in {"config", "xml", "ops"}:
            profile["file_kind_weights"]["config"] = 1.35
            profile["record_kind_weights"]["code"] = 1.1
            profile["relation_bonus"] = 0.2
            profile["generated_penalty"] = 0.12

        if "architecture" in normalized_intent or "overview" in normalized_intent:
            profile["record_kind_weights"]["doc"] = max(1.35, profile["record_kind_weights"]["doc"])
            profile["file_kind_weights"]["doc"] = max(1.3, profile["file_kind_weights"]["doc"])
        if normalized_intent == "code_explanation_with_codecompass":
            profile["record_kind_weights"]["doc"] = max(1.45, profile["record_kind_weights"]["doc"])
            profile["file_kind_weights"]["doc"] = max(1.45, profile["file_kind_weights"]["doc"])
            profile["record_kind_weights"]["code"] = min(1.1, profile["record_kind_weights"]["code"])
        if "bug" in normalized_intent or "error" in normalized_intent or "fix" in normalized_intent:
            profile["record_kind_weights"]["code"] = max(1.35, profile["record_kind_weights"]["code"])
            profile["record_kind_weights"]["relation"] = max(1.2, profile["record_kind_weights"]["relation"])
            profile["symbol_multiplier"] = max(1.2, profile["symbol_multiplier"])
            profile["importance_weight"] = max(0.14, float(profile["importance_weight"]))
        if "dependency" in normalized_intent or "neighbor" in normalized_intent or "symbol" in normalized_intent:
            profile["relation_bonus"] = max(0.4, float(profile["relation_bonus"]))

        return profile

    def _record_field_texts(self, record: dict[str, Any], source_hint: str) -> dict[str, str]:
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

    def current_manifest_identity(self) -> dict[str, Any]:
        """Return the newest immutable snapshot revision, never a host-path hash."""

        indices = sorted(
            list(self._iter_completed_indices()),
            key=lambda item: (
                -float(getattr(item, "updated_at", 0.0) or 0.0),
                str(getattr(item, "id", "") or ""),
            ),
        )
        for index in indices:
            metadata = dict(getattr(index, "index_metadata", None) or {})
            nested = dict(metadata.get("codecompass_snapshot_manifest") or {})
            revision = str(
                metadata.get("codecompass_snapshot_revision")
                or nested.get("snapshot_revision")
                or ""
            ).strip().lower()
            if _is_sha256(revision):
                return {
                    "state": "current",
                    "snapshot_revision": revision,
                    "source_revision": nested.get("source_revision"),
                    "knowledge_index_id": str(getattr(index, "id", "") or ""),
                    "source": "knowledge_index_metadata",
                }
            manifest_path = Path(str(getattr(index, "manifest_path", "") or ""))
            manifest = self._read_manifest_identity(manifest_path)
            if manifest is not None:
                return {
                    **manifest,
                    "knowledge_index_id": str(getattr(index, "id", "") or ""),
                }
        return {
            "state": "degraded",
            "snapshot_revision": None,
            "source_revision": None,
            "knowledge_index_id": None,
            "source": "snapshot_manifest_unavailable",
        }

    @staticmethod
    def _read_manifest_identity(path: Path) -> dict[str, Any] | None:
        if not str(path) or path.is_symlink() or not path.is_file():
            return None
        try:
            if path.stat().st_size > 2_000_000:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        nested = dict(payload.get("codecompass_snapshot_manifest") or payload.get("snapshot_manifest") or {})
        revision = str(
            payload.get("codecompass_snapshot_revision")
            or payload.get("snapshot_revision")
            or nested.get("snapshot_revision")
            or ""
        ).strip().lower()
        if not _is_sha256(revision):
            return None
        return {
            "state": "current",
            "snapshot_revision": revision,
            "source_revision": nested.get("source_revision"),
            "source": "snapshot_manifest",
        }

    def _duplicate_candidate_ids(self, records: list[tuple[str, dict[str, Any]]]) -> set[str]:
        duplicate_ids: set[str] = set()
        for _filename, record in records:
            relation = str(record.get("relation") or record.get("type") or "").strip().lower()
            if relation != "duplicate_candidate":
                continue
            for key in ("from", "to", "source_id", "target_resolved"):
                value = str(record.get(key) or "").strip()
                if value:
                    duplicate_ids.add(value)
        return duplicate_ids

    def _is_boilerplate_candidate(self, record: dict[str, Any], *, source_hint: str, record_kind: str) -> bool:
        source_lower = str(source_hint or "").lower()
        role_labels = {str(item).strip().lower() for item in list(record.get("role_labels") or []) if str(item).strip()}
        if record.get("generated_code"):
            return True
        if any(marker in source_lower for marker in ("generated/", "generated\\", "target/", "build/generated")):
            return True
        if "dto" in role_labels or "value_object" in role_labels:
            return True
        normalized_kind = str(record_kind or "").strip().lower()
        if any(marker in normalized_kind for marker in self.BROAD_SUMMARY_KIND_MARKERS):
            return True
        return False

    def _record_kind_bucket(self, record_kind: str) -> str:
        normalized = str(record_kind or "").strip().lower()
        if any(marker in normalized for marker in self.CODE_KIND_MARKERS):
            return "code"
        if any(marker in normalized for marker in self.DOC_KIND_MARKERS):
            return "doc"
        if any(marker in normalized for marker in self.RELATION_KIND_MARKERS):
            return "relation"
        return "other"

    def _file_kind_bucket(self, source_hint: str) -> str:
        classification = self._file_type_classifier.classify(
            str(source_hint or ""),
            is_text=True,
        )
        if classification is None:
            return "other"
        return self.FILE_KIND_BY_FAMILY.get(
            classification.descriptor.family,
            "other",
        )

    def _weighted_token_hits(self, tokens: list[str], text: str, weight: float) -> float:
        if not tokens or not text:
            return 0.0
        haystack = text.lower()
        score = 0.0
        for token in tokens:
            count = haystack.count(token)
            if count <= 0:
                continue
            score += weight * (1.0 + (count - 1) * 0.2)
        return score

    def _score_record(
        self,
        *,
        query: str,
        record: dict[str, Any],
        query_features: dict[str, list[str]],
        field_texts: dict[str, str],
        record_kind: str,
        source_hint: str,
        profile: dict[str, Any],
        duplicate_ids: set[str],
    ) -> tuple[float, dict[str, float]]:
        query_tokens = list(query_features.get("tokens") or [])
        symbol_tokens = list(query_features.get("symbols") or [])
        if not query_tokens:
            return 0.0, {}

        field_weights = {
            "symbol": 4.2,
            "kind": 2.6,
            "path": 2.1,
            "relations": 2.0,
            "summary": 1.5,
            "content": 1.0,
            "focus": 1.9,
        }
        weighted_hits = {
            field: self._weighted_token_hits(query_tokens, field_texts.get(field, ""), weight)
            for field, weight in field_weights.items()
        }
        symbol_hit_score = self._weighted_token_hits(symbol_tokens, field_texts.get("symbol", ""), 2.5) * float(
            profile.get("symbol_multiplier", 1.0)
        )
        phrase_bonus = 0.0
        compact_haystack = " ".join(field_texts.values()).lower()
        normalized_query = re.sub(r"\s+", " ", str(query or "").strip().lower())
        if normalized_query and normalized_query in compact_haystack:
            phrase_bonus = 1.8

        relation_signal = 0.0
        if field_texts.get("relations"):
            relation_signal += 0.7 + float(profile.get("relation_bonus", 0.0))
        if str(record_kind or "").strip().lower().startswith("relation"):
            relation_signal += 0.4

        record_bucket = self._record_kind_bucket(record_kind)
        file_bucket = self._file_kind_bucket(source_hint)
        record_multiplier = float((profile.get("record_kind_weights") or {}).get(record_bucket, 1.0))
        file_multiplier = float((profile.get("file_kind_weights") or {}).get(file_bucket, 1.0))
        base_score = sum(weighted_hits.values()) + symbol_hit_score + phrase_bonus + relation_signal
        importance_score = float(record.get("importance_score") or 0.0)
        importance_boost = min(0.45, importance_score * float(profile.get("importance_weight", 0.0)))
        record_id = str(record.get("id") or "").strip()
        duplicate_penalty = (
            float(profile.get("duplicate_penalty", 0.0))
            if record_id and record_id in duplicate_ids
            else 0.0
        )
        generated_penalty = float(profile.get("generated_penalty", 0.0)) if bool(record.get("generated_code")) else 0.0
        boilerplate_penalty = (
            float(profile.get("boilerplate_penalty", 0.0))
            if self._is_boilerplate_candidate(record, source_hint=source_hint, record_kind=record_kind)
            else 0.0
        )
        quality_multiplier = max(
            0.35,
            1.0 + importance_boost - duplicate_penalty - generated_penalty - boilerplate_penalty,
        )
        score = base_score * record_multiplier * file_multiplier * quality_multiplier
        return score, {
            "base_score": round(base_score, 4),
            "record_multiplier": round(record_multiplier, 4),
            "file_multiplier": round(file_multiplier, 4),
            "symbol_hit_score": round(symbol_hit_score, 4),
            "relation_signal": round(relation_signal, 4),
            "phrase_bonus": round(phrase_bonus, 4),
            "importance_boost": round(importance_boost, 4),
            "duplicate_penalty": round(duplicate_penalty, 4),
            "generated_penalty": round(generated_penalty, 4),
            "boilerplate_penalty": round(boilerplate_penalty, 4),
            "quality_multiplier": round(quality_multiplier, 4),
            "final_score": round(score, 4),
        }

    def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        artifact_ids: set[str] | None = None,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        source_scopes: set[str] | None = None,
        allowed_index_ids: set[str] | None = None,
        authoritative_scope: Mapping[str, Any] | None = None,
        record_predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[ContextChunk]:
        query_features = self._query_features(query)
        profile = self._task_profile(task_kind, retrieval_intent)
        candidates: list[ContextChunk] = []
        for knowledge_index in self._iter_completed_indices(
            allowed_index_ids=allowed_index_ids,
        ):
            expected_scope = dict(authoritative_scope or {})
            observed_scope = {
                "tenant_id": getattr(knowledge_index, "tenant_id", ""),
                "workspace_id": getattr(knowledge_index, "workspace_id", ""),
                "repository_id": getattr(knowledge_index, "repository_id", ""),
                "revision": (
                    getattr(knowledge_index, "revision", "")
                    or getattr(knowledge_index, "source_revision", "")
                ),
            }
            if any(
                str(observed_scope.get(field) or "")
                and str(observed_scope.get(field)) != str(expected)
                for field, expected in expected_scope.items()
                if field in observed_scope and str(expected or "")
            ):
                continue
            source_scope = (
                str(getattr(knowledge_index, "source_scope", "artifact") or "artifact")
                .strip()
                .lower()
                or "artifact"
            )
            if source_scopes is not None and source_scope not in source_scopes:
                continue
            artifact_id = str(getattr(knowledge_index, "artifact_id", "") or "")
            if artifact_ids is not None and (
                source_scope != "artifact"
                or artifact_id not in artifact_ids
            ):
                # An artifact allow-list is a strict capability boundary. It
                # must never degrade into a filter that admits every other
                # source scope (repo, wiki, registered workspace, ...).
                continue
            collection_ids, collection_names = self._collection_metadata(artifact_id)
            output_dir_raw = getattr(knowledge_index, "output_dir", None)
            if not output_dir_raw:
                continue
            output_dir = Path(output_dir_raw)
            if not output_dir.exists():
                continue
            output_records = list(self._iter_output_records(output_dir))
            duplicate_ids = self._duplicate_candidate_ids(output_records)
            for filename, record in output_records:
                if record_predicate is not None and not record_predicate(record):
                    continue
                source = str(
                    record.get("file")
                    or record.get("path")
                    or record.get("id")
                    or artifact_id
                    or "knowledge-index"
                )
                record_text = self._record_text(record)
                if not record_text:
                    continue
                record_kind = str(record.get("kind", ""))
                field_texts = self._record_field_texts(record, source)
                score, breakdown = self._score_record(
                    query=query,
                    record=record,
                    query_features=query_features,
                    field_texts=field_texts,
                    record_kind=record_kind,
                    source_hint=source,
                    profile=profile,
                    duplicate_ids=duplicate_ids,
                )
                if score <= 0:
                    continue
                raw_metadata = {
                    "knowledge_index_id": str(getattr(knowledge_index, "id", "")),
                    "artifact_id": artifact_id,
                    "record_kind": record_kind,
                    "record_file": filename,
                    "record_id": str(record.get("id") or "").strip() or None,
                    "source_scope": source_scope,
                    "profile_name": str(getattr(knowledge_index, "profile_name", "default")),
                    "collection_ids": collection_ids,
                    "collection_names": collection_names,
                    "retrieval_score_breakdown": breakdown,
                    "task_kind": str(task_kind or "").strip() or None,
                    "retrieval_intent": str(retrieval_intent or "").strip() or None,
                    "importance_score": record.get("importance_score"),
                    "generated_code": bool(record.get("generated_code", False)),
                    "duplicate_candidate": bool(str(record.get("id") or "").strip() in duplicate_ids),
                    "boilerplate_candidate": self._is_boilerplate_candidate(
                        record,
                        source_hint=source,
                        record_kind=record_kind,
                    ),
                    "article_title": str(record.get("article_title") or record.get("title") or "").strip() or None,
                    "section_title": str(record.get("section_title") or record.get("heading") or "").strip() or None,
                    "language": str(record.get("language") or record.get("lang") or "").strip() or None,
                    "wiki_article_id": str(record.get("wiki_article_id") or "").strip() or None,
                    "revision": str(record.get("revision") or record.get("revision_id") or "").strip() or None,
                    "import_revision": str(record.get("import_revision") or "").strip() or None,
                    "import_metadata": dict(record.get("import_metadata") or {}),
                    # CCSH-010: line-range metadata — use from record if present, else mark unknown
                    "start_line": record.get("start_line"),
                    "end_line": record.get("end_line"),
                    "line_range_status": "available" if (
                        record.get("start_line") is not None and record.get("end_line") is not None
                    ) else "unknown",
                    "repo_relative_path": str(record.get("path") or record.get("file") or "").strip() or None,
                    "symbol": str(record.get("symbol") or "").strip() or None,
                    # Provider-issued identity remains unverified here and is
                    # released only after SourceCatalogAuthority validation.
                    "source_id": str(record.get("source_id") or "").strip() or None,
                    "source_version": str(record.get("source_version") or "").strip() or None,
                    "tenant_id": str(record.get("tenant_id") or "").strip() or None,
                    "scope": str(record.get("scope") or record.get("source_scope") or "").strip() or None,
                    "provenance_digest": str(record.get("provenance_digest") or "").strip() or None,
                    "source_provenance": dict(record.get("provenance") or {}),
                    "content_hash": str(record.get("content_hash") or "").strip() or None,
                    "source_manifest_hash": str(record.get("manifest_hash") or "").strip() or None,
                    "line_start": record.get("line_start", record.get("start_line")),
                    "line_end": record.get("line_end", record.get("end_line")),
                }
                candidates.append(
                    ContextChunk(
                        engine="knowledge_index",
                        source=source,
                        content=record_text,
                        score=score,
                        metadata=normalize_chunk_metadata(
                            engine="knowledge_index",
                            source=source,
                            content=record_text,
                            metadata=raw_metadata,
                            verified_source_ids=(),
                        ),
                    )
                )
        ranked = sorted(
            candidates,
            key=lambda item: (-item.score, item.engine, item.source, item.content[:80]),
        )
        return ranked[: max(1, int(top_k))]

    def search_records(
        self,
        query: str,
        *,
        limit: int = 8,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        source_scopes: set[str] | None = None,
        allowed_index_ids: set[str] | None = None,
        authoritative_scope: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Stable dict projection for CodeCompass tools and context planners."""

        chunks = self.search(
            query,
            top_k=max(1, int(limit)),
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            source_scopes=source_scopes,
            allowed_index_ids=allowed_index_ids,
            authoritative_scope=authoritative_scope,
        )
        records: list[dict[str, Any]] = []
        for chunk in chunks:
            metadata = dict(chunk.metadata or {})
            expected_scope = dict(authoritative_scope or {})
            observed_scope = {
                "tenant_id": metadata.get("tenant_id"),
                "workspace_id": metadata.get("workspace_id"),
                "repository_id": metadata.get("repository_id"),
                "revision": metadata.get("revision") or metadata.get("source_revision"),
                "source_scope": metadata.get("source_scope"),
            }
            if any(
                str(observed_scope.get(key) or "")
                and str(observed_scope.get(key)) != str(expected)
                for key, expected in expected_scope.items()
                if key in observed_scope and str(expected or "")
            ):
                continue
            records.append(
                {
                    "id": str(metadata.get("record_id") or metadata.get("chunk_id") or ""),
                    # Keep the immutable record locator distinct from the
                    # display source. An id-only record has no path and must
                    # remain hydratable with an empty path selector.
                    "path": str(metadata.get("repo_relative_path") or ""),
                    "source": str(chunk.source or ""),
                    "content": str(chunk.content or ""),
                    "score": float(chunk.score or 0.0),
                    "symbol": str(metadata.get("symbol") or ""),
                    "kind": str(metadata.get("record_kind") or "retrieval_chunk"),
                    "metadata": metadata,
                    "verification_status": (
                        "verified" if bool(metadata.get("source_id_verified")) else "unverified"
                    ),
                }
            )
        return records


knowledge_index_retrieval_service = KnowledgeIndexRetrievalService()


def get_knowledge_index_retrieval_service() -> KnowledgeIndexRetrievalService:
    return knowledge_index_retrieval_service


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)
