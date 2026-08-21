"""Rebuildable Markdown, graph and retrieval projections."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from agent.repository_map_engine import ContextChunk
from ananta_contracts.knowledge_hygiene import (
    CuratedWikiPage,
    KnowledgeClaim,
    KnowledgeConflict,
    canonical_digest,
)


_SAFE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SAFE_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class ProjectionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class AtomicMarkdownProjector:
    def __init__(self, target_root: Path) -> None:
        self._target_root = target_root

    def project(self, page: CuratedWikiPage) -> Path:
        if not _SAFE_SLUG.fullmatch(page.slug):
            raise ProjectionError("unsafe_wiki_slug")
        if not _SAFE_PROJECT.fullmatch(page.project_id) or page.project_id in {".", ".."}:
            raise ProjectionError("unsafe_project_id")
        project_root = self._target_root / page.project_id
        project_root.mkdir(parents=True, exist_ok=True)
        target = project_root / f"{page.slug}.md"
        rendered = self.render(page)
        fd, temporary = tempfile.mkstemp(prefix=f".{page.slug}.", suffix=".tmp", dir=project_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target

    @staticmethod
    def render(page: CuratedWikiPage) -> str:
        frontmatter = {
            "schema": "curated_wiki_page.v1",
            "page_id": page.page_id,
            "project_id": page.project_id,
            "slug": page.slug,
            "revision": page.revision,
            "content_hash": page.content_hash,
            "coverage": page.coverage.value,
            "source_refs": list(page.source_refs),
            "claim_refs": [[claim_id, revision] for claim_id, revision in page.claim_refs],
            "conflict_refs": list(page.conflict_refs),
            "aliases": list(page.aliases),
        }
        metadata = "\n".join(
            f"{key}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}"
            for key, value in frontmatter.items()
        )
        warning = ""
        if page.conflict_refs:
            warning = "\n> WARNING: Open or review-pending knowledge conflicts: " + ", ".join(page.conflict_refs) + "\n"
        return f"---\n{metadata}\n---\n\n# {page.title}\n{warning}\n{page.body_markdown.rstrip()}\n"


@dataclass(frozen=True, slots=True)
class GraphSupplement:
    version: str
    project_id: str
    basis_hash: str
    supplement_hash: str
    nodes: tuple[dict[str, object], ...]
    edges: tuple[dict[str, object], ...]


def materialize_graph_supplement(
    *,
    project_id: str,
    claims: Sequence[KnowledgeClaim],
    conflicts: Sequence[KnowledgeConflict],
    pages: Sequence[CuratedWikiPage],
) -> GraphSupplement:
    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    for claim in sorted(claims, key=lambda item: (item.claim_id, item.revision)):
        nodes.append(
            {
                "id": f"claim:{claim.claim_id}:{claim.revision}",
                "kind": "knowledge_claim",
                "label": f"{claim.subject}: {claim.predicate}",
                "source_id": claim.source_id,
                "revision": claim.revision,
                "coverage": claim.coverage.value,
            }
        )
    for conflict in sorted(conflicts, key=lambda item: item.conflict_id):
        conflict_node = f"conflict:{conflict.conflict_id}"
        nodes.append(
            {
                "id": conflict_node,
                "kind": "knowledge_conflict",
                "label": conflict.conflict_type,
                "state": conflict.state.value,
                "severity": conflict.severity,
                "coverage": conflict.coverage.value,
                "marker": "knowledge-conflict",
            }
        )
        for claim_id, revision in (
            (conflict.left_claim_id, conflict.left_claim_revision),
            (conflict.right_claim_id, conflict.right_claim_revision),
        ):
            edges.append(
                {
                    "id": f"{conflict_node}:claim:{claim_id}:{revision}",
                    "source": conflict_node,
                    "target": f"claim:{claim_id}:{revision}",
                    "kind": "conflicts_with",
                }
            )
    for page in sorted(pages, key=lambda item: (item.slug, item.revision)):
        page_node = f"wiki:{page.page_id}:{page.revision}"
        nodes.append(
            {
                "id": page_node,
                "kind": "curated_wiki",
                "label": page.title,
                "slug": page.slug,
                "revision": page.revision,
                "coverage": page.coverage.value,
            }
        )
        for claim_id, revision in page.claim_refs:
            edges.append(
                {
                    "id": f"{page_node}:claim:{claim_id}:{revision}",
                    "source": page_node,
                    "target": f"claim:{claim_id}:{revision}",
                    "kind": "grounded_by",
                }
            )
    basis = {
        "project_id": project_id,
        "claims": [[item.claim_id, item.revision, item.record_digest] for item in claims],
        "conflicts": [[item.conflict_id, item.version, item.basis_digest] for item in conflicts],
        "pages": [[item.page_id, item.revision, item.content_hash] for item in pages],
    }
    basis_hash = canonical_digest(basis)
    supplement_hash = canonical_digest(
        {
            "version": "knowledge_graph_supplement.v1",
            "basis_hash": basis_hash,
            "nodes": nodes,
            "edges": edges,
        }
    )
    return GraphSupplement(
        version="knowledge_graph_supplement.v1",
        project_id=project_id,
        basis_hash=basis_hash,
        supplement_hash=supplement_hash,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


class CuratedWikiRetrievalAdapter:
    """Additive source adapter; original evidence remains cited in metadata."""

    source_type = "curated_wiki"

    def __init__(self, page_loader: Callable[[str], Iterable[CuratedWikiPage]]) -> None:
        self._page_loader = page_loader

    def search(
        self,
        query: str,
        *,
        project_id: str,
        limit: int = 10,
        **_: object,
    ) -> list[ContextChunk]:
        tokens = {token for token in re.findall(r"[a-z0-9]+", query.casefold()) if len(token) > 1}
        candidates: list[tuple[float, CuratedWikiPage]] = []
        for page in self._page_loader(project_id):
            haystack = f"{page.title} {page.body_markdown}".casefold()
            score = sum(1.0 for token in tokens if token in haystack) / max(len(tokens), 1)
            if score > 0.0 or not tokens:
                candidates.append((score, page))
        candidates.sort(key=lambda item: (-item[0], item[1].slug, -item[1].revision))
        return [
            ContextChunk(
                engine="curated_wiki",
                source=f"curated-wiki://{page.project_id}/{page.slug}@{page.revision}",
                content=page.body_markdown,
                score=score,
                metadata={
                    "source_type": "curated_wiki",
                    "page_id": page.page_id,
                    "page_revision": str(page.revision),
                    "claim_refs": json.dumps(page.claim_refs, separators=(",", ":")),
                    "source_refs": json.dumps(page.source_refs, separators=(",", ":")),
                    "conflict_refs": json.dumps(page.conflict_refs, separators=(",", ":")),
                    "coverage": page.coverage.value,
                    "content_hash": page.content_hash,
                    "authority": "supplement_only",
                },
            )
            for score, page in candidates[: max(1, min(int(limit), 100))]
        ]
