"""Deterministic project-wide resolution for relations emitted per file.

Per-file extractors remain independent and testable.  This post-processing
step owns only cross-file identity matching; it never reads files, executes
content or creates orchestration work.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

_DOCUMENT_EXTENSIONS = {"md", "mdx", "rst", "adoc"}
_EXTERNAL_SCHEMES = {"http", "https", "mailto", "ftp", "data", "javascript"}


def _normalized_path(value: str) -> str:
    return posixpath.normpath(value.replace("\\", "/").lstrip("/"))


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s-]", "", unquote(value).lower())
    return re.sub(r"[\s-]+", "-", normalized).strip("-")


@dataclass(frozen=True, slots=True)
class _Target:
    target_id: str
    file: str


@dataclass(slots=True)
class _ResolutionIndex:
    files: dict[str, list[_Target]]
    document_sections: dict[tuple[str, str], list[_Target]]
    angular_selectors: dict[str, list[_Target]]
    dockerfiles: dict[str, list[_Target]]

    @classmethod
    def build(cls, index_records: list[dict], detail_records: list[dict]) -> "_ResolutionIndex":
        files: dict[str, list[_Target]] = {}
        dockerfiles: dict[str, list[_Target]] = {}
        document_sections: dict[tuple[str, str], list[_Target]] = {}
        angular_selectors: dict[str, list[_Target]] = {}

        for record in index_records:
            rel_path = record.get("file")
            record_id = record.get("id")
            if not isinstance(rel_path, str) or not isinstance(record_id, str):
                continue
            normalized = _normalized_path(rel_path)
            target = _Target(record_id, normalized)
            if str(record.get("kind") or "").endswith("_file"):
                files.setdefault(normalized, []).append(target)
            if record.get("kind") == "dockerfile_file":
                dockerfiles.setdefault(normalized, []).append(target)

        for record in [*index_records, *detail_records]:
            rel_path = record.get("file")
            record_id = record.get("id")
            if not isinstance(rel_path, str) or not isinstance(record_id, str):
                continue
            normalized = _normalized_path(rel_path)
            kind = str(record.get("kind") or "")
            if kind in {"md_section", "mdx_section", "rst_section", "adoc_section"}:
                anchors = {
                    str(record.get("anchor") or "").lstrip("#"),
                    _slug(str(record.get("heading") or record.get("title") or record.get("name") or "")),
                }
                for anchor in anchors - {""}:
                    document_sections.setdefault((normalized, anchor), []).append(_Target(record_id, normalized))
            if kind == "typescript_class":
                metadata = record.get("angular_metadata")
                selector_value = metadata.get("selector") if isinstance(metadata, dict) else None
                if isinstance(selector_value, str):
                    for selector in (item.strip() for item in selector_value.split(",")):
                        if selector:
                            angular_selectors.setdefault(selector, []).append(_Target(record_id, normalized))

        for mapping in (files, document_sections, angular_selectors, dockerfiles):
            for key, values in mapping.items():
                mapping[key] = sorted({value for value in values}, key=lambda item: (item.file, item.target_id))
        return cls(files, document_sections, angular_selectors, dockerfiles)


def resolve_cross_file_relations(
    index_records: list[dict],
    detail_records: list[dict],
    relation_records: list[dict],
) -> dict[str, int]:
    """Resolve supported project-wide references in place and return counts."""

    resolution_index = _ResolutionIndex.build(index_records, detail_records)
    stats = {
        "resolved": 0,
        "ambiguous": 0,
        "external": 0,
        "blocked_outside_repository": 0,
        "unresolved": 0,
    }
    for relation in relation_records:
        relation_type = relation.get("relation")
        if relation.get("target_resolved"):
            continue
        candidates: list[_Target] = []
        status_override: str | None = None
        if relation_type == "uses_component_selector":
            candidates = resolution_index.angular_selectors.get(str(relation.get("target") or ""), [])
        elif relation_type == "uses_dockerfile":
            candidates, status_override = _dockerfile_candidates(relation, resolution_index)
        elif relation_type in {"references_document", "references_anchor"}:
            candidates, status_override = _document_candidates(relation, resolution_index)
        else:
            continue

        if len(candidates) == 1:
            target = candidates[0]
            relation["target_resolved"] = target.target_id
            relation["target_file"] = target.file
            relation["resolution_status"] = "resolved"
            stats["resolved"] += 1
        elif len(candidates) > 1:
            relation["resolution_status"] = "ambiguous"
            relation["resolution_candidates"] = [
                {"target_id": item.target_id, "file": item.file} for item in candidates
            ]
            stats["ambiguous"] += 1
        else:
            status = status_override or "unresolved"
            relation["resolution_status"] = status
            stats[status] += 1
    return stats


def _dockerfile_candidates(relation: dict, resolution_index: _ResolutionIndex) -> tuple[list[_Target], str | None]:
    source_file = _normalized_path(str(relation.get("file") or ""))
    target = str(relation.get("target") or "").strip()
    context = str(relation.get("build_context") or ".").strip()
    if not target:
        return [], None
    base_dir = posixpath.dirname(source_file)
    context_path = _join_repository_path(base_dir, context)
    if context_path is None:
        return [], "blocked_outside_repository"
    target_path = _join_repository_path(context_path, target)
    if target_path is None:
        return [], "blocked_outside_repository"
    exact = resolution_index.dockerfiles.get(target_path, [])
    if exact:
        return exact, None
    # A unique basename fallback supports Compose files whose context is
    # omitted while retaining honest ambiguity for monorepos.
    basename = posixpath.basename(target_path).lower()
    matches = [
        item
        for path, values in resolution_index.dockerfiles.items()
        if posixpath.basename(path).lower() == basename
        for item in values
    ]
    return sorted(matches, key=lambda item: (item.file, item.target_id)), None


def _document_candidates(relation: dict, resolution_index: _ResolutionIndex) -> tuple[list[_Target], str | None]:
    raw_target = str(relation.get("target") or "").strip().strip("<>")
    if not raw_target:
        return [], None
    split = urlsplit(raw_target)
    if split.scheme.lower() in _EXTERNAL_SCHEMES or raw_target.startswith("//"):
        return [], "external"
    if split.scheme:
        return [], "blocked_outside_repository"
    source_file = _normalized_path(str(relation.get("file") or ""))
    source_dir = posixpath.dirname(source_file)
    target_path_raw = unquote(split.path)
    anchor = _slug(split.fragment)

    if not target_path_raw:
        target_path = source_file
    elif target_path_raw.startswith("/"):
        target_path = _normalized_path(target_path_raw)
    else:
        joined = _join_repository_path(source_dir, target_path_raw)
        if joined is None:
            return [], "blocked_outside_repository"
        target_path = joined

    path_candidates = [target_path]
    if posixpath.splitext(target_path)[1].lstrip(".").lower() not in _DOCUMENT_EXTENSIONS:
        path_candidates.extend(
            [f"{target_path}.md", posixpath.join(target_path, "README.md"), posixpath.join(target_path, "index.md")]
        )
    if anchor:
        candidates = [
            item for path in path_candidates for item in resolution_index.document_sections.get((path, anchor), [])
        ]
    else:
        candidates = [item for path in path_candidates for item in resolution_index.files.get(path, [])]
    return sorted({item for item in candidates}, key=lambda item: (item.file, item.target_id)), None


def _join_repository_path(base: str, relative: str) -> str | None:
    normalized_relative = relative.replace("\\", "/")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized_relative):
        return None
    joined = posixpath.normpath(posixpath.join(base, normalized_relative))
    if joined == ".." or joined.startswith("../"):
        return None
    return joined[2:] if joined.startswith("./") else joined
