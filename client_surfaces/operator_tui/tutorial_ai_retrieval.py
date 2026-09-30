"""Retrieval helpers of the tutorial AI: RAG scoring, embeddings and hint loading.

Split out of ``tutorial_ai_engine`` (which re-exports every name) so that the
engine module keeps the LLM/worker interaction and this module the pure,
file-based retrieval helpers.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path

# ── Scoring helpers ──────────────────────────────────────────────────────


def _score_rag_record(text: str, query_tokens: list[str]) -> float:
    haystack = str(text or "").lower()
    if not haystack:
        return 0.0
    if not query_tokens:
        return 0.5
    score = 0.0
    for token in query_tokens:
        count = haystack.count(token)
        if count <= 0:
            continue
        score += 1.0 + min(0.6, (count - 1) * 0.15)
    return score


def _score_rag_record_with_embedding(
    compact: str,
    embedding_text: str,
    query_tokens: list[str],
    source_kind: str,
) -> float:
    base = _score_rag_record(compact, query_tokens)
    if embedding_text and query_tokens:
        emb_score = _score_rag_record(embedding_text.lower(), query_tokens)
        base += emb_score * 2.0
    if source_kind in {"embedding", "graph_nodes", "graph_edges"}:
        base += 0.8
    return base


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    lnorm = math.sqrt(sum(a * a for a in left))
    rnorm = math.sqrt(sum(b * b for b in right))
    if lnorm <= 0.0 or rnorm <= 0.0:
        return 0.0
    return dot / (lnorm * rnorm)


def _embedding_vector_for_text(text: str) -> list[float]:
    api_base = str(
        os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or ""
    ).strip()
    if not api_base:
        return []
    model = str(
        os.environ.get("ANANTA_TUI_CHAT_EMBEDDING_MODEL")
        or os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL")
        or ""
    ).strip()
    token = str(
        os.environ.get("ANANTA_TUI_SNAKE_AI_API_TOKEN")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    body = json.dumps({"model": model, "input": text[:1200]}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        api_base.rstrip("/") + "/embeddings",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return []
    embedding = data[0].get("embedding") if isinstance(data[0], dict) else None
    if not isinstance(embedding, list):
        return []
    result: list[float] = []
    for item in embedding:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            return []
    return result


# ── CodeCompass hint loading ──────────────────────────────────────────────


def _load_codecompass_hints_from_dir(out_dir: Path) -> list[str]:
    try:
        from worker.retrieval.codecompass_candidate_resolver import (
            CodeCompassCandidateResolver,
            ResolverConfig,
            _classify_path,
        )
        mode = ResolverConfig.from_env()
    except Exception:
        return []

    try:
        index_path = out_dir / "index.jsonl"
        if not index_path.exists():
            return []
        _FILE_KINDS = {
            "python_file", "python", "py_file",
            "md_file", "markdown_file",
            "java_file", "java",
            "typescript_file", "ts_file", "tsx_file",
            "javascript_file", "js_file",
            "yaml_file", "yml_file",
            "json_file", "toml_file",
            "shell_file", "bash_file",
            "config_file", "compose_file", "dockerfile",
            "xml_file", "html_file", "css_file",
        }
        seen: set[str] = set()
        ordered: list[tuple[str, float]] = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            kind = str(rec.get("kind") or "").strip().lower()
            if "module" in kind and "summary" in kind:
                continue
            if _FILE_KINDS and kind not in _FILE_KINDS:
                pass
            file_path = str(rec.get("file") or rec.get("path") or "").strip()
            if not file_path or file_path in seen:
                continue
            if "/" not in file_path and "\\" not in file_path and "." not in file_path:
                continue
            if not mode.accepts(file_path):
                continue
            seen.add(file_path)
            kind_class = _classify_path(file_path)
            priority = 0.0 if kind_class == "source" else 1.0
            ordered.append((file_path, priority))
        ordered.sort(key=lambda x: (x[1], x[0]))
        return [path for path, _ in ordered[:64]]
    except Exception:
        return []


def _load_rag_context_from_dir(
    out_dir: Path,
    query_tokens: list[str],
    top_k: int,
    max_records_per_file: int,
    scope_filter: str = "tui_only",
) -> list[str]:
    scope_full = scope_filter == "full" or str(
        os.environ.get("ANANTA_TUI_RAG_SCOPE_FILTER", "")
    ).strip().lower() == "full"

    try:
        from worker.retrieval.codecompass_candidate_resolver import (
            CodeCompassCandidateResolver,
            ResolverConfig,
        )
        mode = ResolverConfig.from_env()
        resolver = CodeCompassCandidateResolver(max_candidates=top_k * 4)
        question_text = " ".join(query_tokens)
        candidates = resolver.resolve(
            question=question_text,
            output_dir=out_dir,
            mode=mode,
        )
        manifest_path = out_dir / "manifest.json"
        manifest_files: list[str] = []
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
            partitioned = manifest.get("partitioned_outputs") if isinstance(manifest, dict) else None
            if isinstance(partitioned, dict):
                for values in partitioned.values():
                    if isinstance(values, list):
                        for item in values:
                            rel = str(item or "").strip()
                            if rel:
                                manifest_files.append(rel)
        resolver_top_files = [c["path"] for c in candidates[: top_k * 2]]
        files_to_scan: list[str] = []
        seen: set[str] = set()
        for rel in manifest_files + resolver_top_files + [
            "context.jsonl", "details.jsonl", "index.jsonl",
            "xml_overview.jsonl", "embedding.jsonl",
            "graph_nodes.jsonl", "graph_edges.jsonl", "relations.jsonl",
        ]:
            norm = rel.strip().lstrip("/")
            if norm and norm not in seen:
                seen.add(norm)
                files_to_scan.append(norm)
        files_to_scan = files_to_scan[:24]
    except Exception:
        files_to_scan = [
            "context.jsonl", "details.jsonl", "index.jsonl",
            "xml_overview.jsonl", "embedding.jsonl",
            "graph_nodes.jsonl", "graph_edges.jsonl", "relations.jsonl",
        ]

    candidates_list: list[tuple[float, str]] = []
    embedding_api_calls = 0
    use_embedding_api = str(os.environ.get("ANANTA_TUI_CHAT_USE_EMBEDDING_API", "")).strip().lower() in {"1", "true", "yes", "on"}
    query_embedding = _embedding_vector_for_text(" ".join(query_tokens)) if use_embedding_api and query_tokens else []

    for rel in files_to_scan:
        path = out_dir / rel
        if not path.exists() or not path.is_file() or path.suffix.lower() != ".jsonl":
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        source_kind = path.name.lower().replace(".jsonl", "")
        for idx, line in enumerate(lines):
            if idx >= max_records_per_file:
                break
            payload = line.strip()
            if not payload:
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            source_file = str(
                parsed.get("file") or parsed.get("path") or parsed.get("source_file")
                or parsed.get("source_path") or parsed.get("target_file")
                or parsed.get("target_path") or ""
            ).strip()
            if not scope_full and source_kind in {"graph_nodes", "graph_edges"} and source_file:
                sl = source_file.lower()
                if "client_surfaces/operator_tui" not in sl and "/operator_tui/" not in sl:
                    tgt = str(parsed.get("target_file") or parsed.get("target_path") or "").lower()
                    if "client_surfaces/operator_tui" not in tgt and "/operator_tui/" not in tgt:
                        continue
            embedding_text = str(parsed.get("embedding_text") or "").strip()
            tokens = [
                str(parsed.get("domain") or "").strip(),
                str(parsed.get("kind") or "").strip(),
                str(parsed.get("title") or "").strip(),
                str(parsed.get("section_title") or "").strip(),
                str(parsed.get("name") or "").strip(),
                source_file,
                str(parsed.get("source_id") or parsed.get("from") or "").strip(),
                str(parsed.get("target_id") or parsed.get("to") or "").strip(),
                str(parsed.get("relation") or parsed.get("type") or "").strip(),
                embedding_text,
                str(parsed.get("summary") or "").strip(),
                str(parsed.get("content") or parsed.get("text") or "").strip(),
            ]
            text = " · ".join(part for part in tokens if part)
            compact = " ".join(text.split())
            if not compact:
                continue
            compact = f"{source_kind} · {compact}"
            score = _score_rag_record_with_embedding(compact, embedding_text, query_tokens, source_kind)
            if query_embedding and embedding_text:
                embedding_api_limit = max(1, min(128, int(os.environ.get("ANANTA_TUI_CHAT_EMBEDDING_API_MAX_RECORDS", "64"))))
                if embedding_api_calls < embedding_api_limit:
                    embedding_api_calls += 1
                    candidate_embedding = _embedding_vector_for_text(embedding_text)
                    score += max(0.0, _cosine_similarity(query_embedding, candidate_embedding)) * 4.0
            if "client_surfaces/operator_tui" in compact.lower():
                score += 1.2
            if score <= 0:
                continue
            candidates_list.append((score, compact[:240]))

    ranked = sorted(candidates_list, key=lambda item: item[0], reverse=True)
    results = [item[1] for item in ranked[:top_k]]

    name_hits = _name_lookup_from_details(out_dir, query_tokens, already_found=set(results))
    return name_hits + results if name_hits else results


def _name_lookup_from_details(
    out_dir: Path,
    query_tokens: list[str],
    already_found: set[str],
) -> list[str]:
    name_stopwords = {
        "was", "wie", "ist", "sind", "und", "oder", "the", "what", "how", "does",
        "erkläre", "erklaere", "zeige", "mir", "bitte",
    }
    name_tokens = [
        t.lower()
        for t in query_tokens
        if len(t) >= 3
        and t.lower() not in name_stopwords
        and (t.startswith("_") or "_" in t or len(t) >= 5)
    ]
    if not name_tokens:
        return []
    candidate_paths: list[Path] = []
    index_by_kind = out_dir / "index_by_kind"
    if index_by_kind.exists() and index_by_kind.is_dir():
        for path in sorted(index_by_kind.glob("*.jsonl")):
            name = path.name.lower()
            if any(part in name for part in ("class", "function", "method", "symbol", "python")):
                candidate_paths.append(path)
    for path in [out_dir / "details.jsonl", out_dir / "index.jsonl"]:
        if path.exists():
            candidate_paths.append(path)
    deduped_paths: list[Path] = []
    seen_paths: set[Path] = set()
    for path in candidate_paths:
        if path not in seen_paths and path.exists() and path.is_file():
            seen_paths.add(path)
            deduped_paths.append(path)
    if not deduped_paths:
        return []
    hits: list[str] = []
    for details_path in deduped_paths:
        try:
            for raw_line in details_path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                try:
                    rec = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                name = str(rec.get("name") or rec.get("symbol") or "").strip()
                name_l = name.lower()
                if not name_l:
                    continue
                if any(t in name_l or t.replace("_", "") in name_l.replace("_", "") for t in name_tokens):
                    kind = str(rec.get("kind") or "").strip()
                    fpath = str(rec.get("file") or rec.get("path") or "").strip()
                    emb = str(rec.get("embedding_text") or rec.get("summary") or "").strip()
                    entry = f"detail · {kind} · {name} · {fpath}" + (f" · {emb[:120]}" if emb else "")
                    compact = " ".join(entry.split())[:240]
                    if compact not in already_found and compact not in hits:
                        hits.append(compact)
                if len(hits) >= 8:
                    return hits
        except Exception:
            continue
    return hits
