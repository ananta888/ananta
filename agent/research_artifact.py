import json
import re
import time
from typing import Any

from agent.research_backend import is_research_backend, resolve_research_backend_config


def _extract_source_records(text: str) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    sources: list[dict[str, Any]] = []
    for match in re.findall(r"https?://[^\s)\]>\"']+", text):
        url = match.rstrip(".,;:")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append({"title": url, "url": url, "kind": "web", "confidence": 0.5})
    return sources


def _extract_citation_records(text: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    known_urls = {str(source.get("url") or ""): source for source in sources}
    for line in text.splitlines():
        snippet = line.strip()
        if not snippet:
            continue
        for match in re.findall(r"https?://[^\s)\]>\"']+", snippet):
            url = match.rstrip(".,;:")
            source = known_urls.get(url) or {}
            citations.append(
                {
                    "label": str(source.get("title") or url),
                    "excerpt": snippet[:400],
                    "url": url,
                    "source_title": source.get("title"),
                    "kind": source.get("kind") or "web",
                    "confidence": source.get("confidence"),
                }
            )
    return citations


def normalize_research_artifact(
    raw_text: str,
    backend: str = "deerflow",
    task_id: str | None = None,
    cli_result: dict | None = None,
    trace: dict | None = None,
    research_context: dict | None = None,
) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    summary = (paragraphs[0] if paragraphs else text).strip()
    if len(summary) > 400:
        summary = summary[:397].rstrip() + "..."

    cfg = resolve_research_backend_config(provider_override=backend) if is_research_backend(backend) else {
        "provider": backend,
        "display_name": backend,
        "mode": "cli",
        "command": "",
        "working_dir": None,
        "result_format": "markdown",
        "selected": False,
        "selected_provider": None,
    }
    sources = _extract_source_records(text)
    citations = _extract_citation_records(text, sources)
    ready = bool(text and sources)
    trace_payload = dict(trace or {})
    trace_payload.setdefault("provider", backend)
    trace_payload.setdefault(
        "artifact_extraction",
        {
            "source_count": len(sources),
            "citation_count": len(citations),
            "result_format": cfg.get("result_format") or "markdown",
        },
    )

    artifact = {
        "kind": "research_report",
        "summary": summary,
        "report_markdown": text,
        "sources": sources,
        "citations": citations,
        "trace": trace_payload,
        "verification": {
            "ready": ready,
            "passed": ready,
            "has_sources": bool(sources),
            "has_citations": bool(citations),
            "has_report": bool(text),
            "source_count": len(sources),
            "citation_count": len(citations),
            "reason": "verified" if ready else "missing_sources_or_report",
        },
        "backend_metadata": {
            "backend": backend,
            "display_name": cfg.get("display_name") or backend,
            "task_id": task_id,
            "generated_at": int(time.time()),
            "source_count": len(sources),
            "citation_count": len(citations),
            "mode": cfg.get("mode"),
            "command": cfg.get("command"),
            "working_dir": cfg.get("working_dir"),
            "result_format": cfg.get("result_format") or "markdown",
            "selected_provider": cfg.get("selected_provider"),
            "selected": bool(cfg.get("selected")),
            "cli_result": cli_result or {},
            "research_context": {
                "artifact_ids": list((research_context or {}).get("artifact_ids") or []),
                "knowledge_collection_ids": list((research_context or {}).get("knowledge_collection_ids") or []),
                "repo_scope_refs": list((research_context or {}).get("repo_scope_refs") or []),
                "truncated": bool((research_context or {}).get("truncated")),
            }
            if research_context
            else None,
        },
    }
    return artifact
