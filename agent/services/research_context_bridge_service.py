from __future__ import annotations

from pathlib import Path

from agent.models import ResearchContextSummaryContract
from agent.services.ingestion_service import get_ingestion_service
from agent.services.knowledge_index_retrieval_service import get_knowledge_index_retrieval_service
from agent.services.repository_registry import get_repository_registry


class ResearchContextBridgeService:
    """Builds bounded research context from existing artifact, knowledge, and repo seams."""

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _clip_text(self, value: str | None, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    def _safe_repo_path(self, raw_path: str | None) -> Path | None:
        value = str(raw_path or "").strip()
        if not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (self._repo_root() / candidate).resolve()
        else:
            candidate = candidate.resolve()
        repo_root = self._repo_root().resolve()
        if candidate == repo_root or repo_root in candidate.parents:
            return candidate
        return None

    def _ensure_document(self, artifact_id: str):
        repos = get_repository_registry()
        documents = repos.extracted_document_repo.get_by_artifact(artifact_id)
        if documents:
            return documents[-1]
        _, _, document = get_ingestion_service().extract_artifact(artifact_id)
        return document

    def _artifact_context(self, artifact_ids: list[str], *, include_extracted_text: bool, per_item_limit: int) -> list[dict]:
        repos = get_repository_registry()
        items: list[dict] = []
        for artifact_id in artifact_ids:
            artifact = repos.artifact_repo.get_by_id(artifact_id)
            if artifact is None:
                continue
            document = self._ensure_document(artifact_id)
            excerpt = ""
            if include_extracted_text and document is not None:
                excerpt = self._clip_text(document.text_content, per_item_limit)
            items.append(
                {
                    "artifact_id": artifact.id,
                    "filename": artifact.latest_filename,
                    "media_type": artifact.latest_media_type,
                    "status": artifact.status,
                    "extraction_mode": getattr(document, "extraction_mode", None),
                    "excerpt": excerpt or None,
                    "document_metadata": dict(getattr(document, "document_metadata", None) or {}),
                }
            )
        return items

    def _knowledge_context(self, collection_ids: list[str], *, query: str, top_k: int, per_item_limit: int) -> list[dict]:
        repos = get_repository_registry()
        retrieval = get_knowledge_index_retrieval_service()
        items: list[dict] = []
        for collection_id in collection_ids:
            collection = repos.knowledge_collection_repo.get_by_id(collection_id)
            if collection is None:
                continue
            links = repos.knowledge_link_repo.get_by_collection(collection_id)
            artifact_ids = {str(link.artifact_id) for link in links if getattr(link, "artifact_id", None)}
            chunks = retrieval.search(query, top_k=max(1, top_k), artifact_ids=artifact_ids) if artifact_ids and query else []
            items.append(
                {
                    "collection_id": collection.id,
                    "name": collection.name,
                    "description": collection.description,
                    "artifact_ids": sorted(artifact_ids),
                    "chunks": [
                        {
                            "source": chunk.source,
                            "content": self._clip_text(chunk.content, per_item_limit),
                            "score": round(float(chunk.score), 3),
                            "metadata": dict(chunk.metadata or {}),
                        }
                        for chunk in chunks
                    ],
                }
            )
        return items

    def _repo_scope_context(self, repo_scope_refs: list[dict], *, per_item_limit: int) -> list[dict]:
        items: list[dict] = []
        for raw_ref in repo_scope_refs:
            ref = dict(raw_ref or {})
            url = str(ref.get("url") or "").strip() or None
            safe_path = self._safe_repo_path(ref.get("path"))
            if safe_path is None:
                items.append(
                    {
                        "path": ref.get("path"),
                        "ref": ref.get("ref"),
                        "url": url,
                        "status": "external_reference" if url else "path_outside_repo",
                    }
                )
                continue
            if not safe_path.exists():
                items.append(
                    {
                        "path": str(ref.get("path") or ""),
                        "ref": ref.get("ref"),
                        "url": url,
                        "status": "path_not_found",
                    }
                )
                continue
            if safe_path.is_dir():
                entries = sorted(str(path.relative_to(self._repo_root())) for path in safe_path.iterdir())[:12]
                items.append(
                    {
                        "path": str(safe_path.relative_to(self._repo_root())),
                        "ref": ref.get("ref"),
                        "url": url,
                        "status": "directory",
                        "entries": entries,
                    }
                )
                continue
            excerpt = self._clip_text(safe_path.read_text(encoding="utf-8", errors="ignore"), per_item_limit)
            items.append(
                {
                    "path": str(safe_path.relative_to(self._repo_root())),
                    "ref": ref.get("ref"),
                    "url": url,
                    "status": "file",
                    "excerpt": excerpt or None,
                }
            )
        return items

    def _render_prompt_section(self, *, artifacts: list[dict], knowledge_collections: list[dict], repo_scopes: list[dict]) -> str:
        sections: list[str] = []
        if artifacts:
            lines = []
            for item in artifacts:
                excerpt = str(item.get("excerpt") or "").strip()
                lines.append(f"- Artifact {item.get('artifact_id')} ({item.get('filename') or 'unknown'}):")
                if excerpt:
                    lines.append(excerpt)
            sections.append("Artefakt-Kontext:\n" + "\n".join(lines))
        if knowledge_collections:
            lines = []
            for item in knowledge_collections:
                lines.append(f"- Collection {item.get('name') or item.get('collection_id')}:")
                for chunk in list(item.get("chunks") or [])[:5]:
                    lines.append(f"  - {chunk.get('source')}: {chunk.get('content')}")
            sections.append("Knowledge-Kontext:\n" + "\n".join(lines))
        if repo_scopes:
            lines = []
            for item in repo_scopes:
                lines.append(f"- Repo-Scope {item.get('path') or item.get('url')}: {item.get('status')}")
                if item.get("excerpt"):
                    lines.append(str(item["excerpt"]))
                if item.get("entries"):
                    lines.append("Entries: " + ", ".join(item["entries"]))
            sections.append("Repo-Kontext:\n" + "\n".join(lines))
        return "\n\n".join(section for section in sections if section.strip())

    def build_context(self, *, task: dict | None, research_context, query: str | None) -> dict | None:
        payload = research_context.model_dump() if hasattr(research_context, "model_dump") else dict(research_context or {})
        artifact_ids = [str(item).strip() for item in list(payload.get("artifact_ids") or []) if str(item).strip()]
        collection_ids = [str(item).strip() for item in list(payload.get("knowledge_collection_ids") or []) if str(item).strip()]
        repo_scope_refs = [dict(item or {}) for item in list(payload.get("repo_scope_refs") or [])]
        if not artifact_ids and not collection_ids and not repo_scope_refs:
            return None
        max_chars = max(1000, min(int(payload.get("max_context_chars") or 12000), 40000))
        top_k = max(1, min(int(payload.get("top_k") or 5), 10))
        per_item_limit = max(300, min(max_chars // 4, 3000))
        include_extracted_text = bool(payload.get("include_extracted_text", True))
        search_query = str(query or (task or {}).get("description") or "").strip()

        artifacts = self._artifact_context(artifact_ids, include_extracted_text=include_extracted_text, per_item_limit=per_item_limit)
        knowledge_collections = self._knowledge_context(collection_ids, query=search_query, top_k=top_k, per_item_limit=per_item_limit)
        repo_scopes = self._repo_scope_context(repo_scope_refs, per_item_limit=per_item_limit)
        prompt_section = self._render_prompt_section(
            artifacts=artifacts,
            knowledge_collections=knowledge_collections,
            repo_scopes=repo_scopes,
        )
        truncated = len(prompt_section) > max_chars
        prompt_section = self._clip_text(prompt_section, max_chars) if prompt_section else None
        return ResearchContextSummaryContract(
            artifact_ids=artifact_ids,
            knowledge_collection_ids=collection_ids,
            repo_scope_refs=repo_scope_refs,
            artifacts=artifacts,
            knowledge_collections=knowledge_collections,
            repo_scopes=repo_scopes,
            prompt_section=prompt_section,
            truncated=truncated,
            context_char_count=len(prompt_section or ""),
        ).model_dump(exclude_none=True)


research_context_bridge_service = ResearchContextBridgeService()


def get_research_context_bridge_service() -> ResearchContextBridgeService:
    return research_context_bridge_service
