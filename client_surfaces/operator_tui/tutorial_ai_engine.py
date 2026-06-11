"""Tutorial AI engine — LLM interaction and hint generation for tutorials.

Contains: module-level helper functions (_score_rag_record, _cosine_similarity,
          _embedding_vector_for_text, _load_codecompass_hints_from_dir,
          _load_rag_context_from_dir, _name_lookup_from_details),
          data constants, and TutorialAiEngineMixin with AI/LLM methods.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from client_surfaces.operator_tui.keybindings_config import display_for_action

if TYPE_CHECKING:
    from concurrent.futures import Future, ThreadPoolExecutor


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
            CodeCompassCandidateResolver, ResolverConfig, _classify_path,
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
            CodeCompassCandidateResolver, ResolverConfig,
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


# ── Data constants ────────────────────────────────────────────────────────


_TUTORIAL_AI_KNOWLEDGE: tuple[str, ...] = (
    f"TUI: Focus [{display_for_action('cycle_focus_or_channel', 'Ctrl+W')}], Command [:], "
    f"Snake [{display_for_action('toggle_snake_mode', 'Ctrl+S')}], "
    f"Hilfe [{display_for_action('help', 'Ctrl+Y')}].",
    "Snake: B frame-mode, X Rahmen, C copy, V replace (nur command line).",
    "Architektur: Hub orchestriert, Worker fuehren aus; keine worker-zu-worker orchestration.",
    "Taskfluss: User -> Hub -> Task Queue -> Worker; Hub bleibt Control Plane.",
    "Betrieb: Hub/Worker getrennte Container, reproduzierbare Umgebungen.",
    "API evolution: additive, rueckwaertskompatibel, keine Big-Bang Refactors.",
)
_TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT = (
    "You are tutorial-snake guidance.\n"
    "Priority: {priority}\n"
    "User feed: {user_feed}\n"
    "Contact zone: {contact_zone}\n"
    "Respond with one immediate actionable hint (max 180 chars)."
)


# ── TutorialAiEngineMixin ────────────────────────────────────────────────


class TutorialAiEngineMixin:
    """Mixin providing AI/LLM tutorial methods."""

    def _tutorial_ai_tip(self, *, now: float) -> str:
        status = self._tutorial_status_delta_summary()
        game = self.state.header_logo_game if isinstance(getattr(self.state, "header_logo_game", None), dict) else {}
        cfg_override = game.get("ai_visual_use_codecompass")
        include_external_context = (
            bool(cfg_override)
            if isinstance(cfg_override, bool)
            else str(os.environ.get("ANANTA_TUI_VISUAL_AI_USE_CODECOMPASS", "0")).strip().lower() in {"1", "true", "yes", "on"}
        )
        hints = self._load_codecompass_hints(now=now) if include_external_context else []
        rag_context = self._load_rag_helper_context(now=now) if include_external_context else []
        if not self._tutorial_async_enabled():
            result = self._tutorial_ai_tip_sync(now=now, status=status, hints=hints, rag_context=rag_context)
            if result:
                self._tutorial_last_source = result.get("source", self._tutorial_last_source)
                self._tutorial_last_target = result.get("target", self._tutorial_last_target)
                self._tutorial_last_tip_text = result.get("text", self._tutorial_last_tip_text)
            return self._tutorial_last_tip_text

        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", "8.0"))))
        self._poll_tutorial_async_tip_result()
        if self._tutorial_async_tip_future is None and now >= self._tutorial_async_next_refresh_at:
            self._tutorial_async_next_refresh_at = now + refresh_seconds
            self._tutorial_async_tip_future = self._tutorial_async_tip_executor.submit(
                self._tutorial_ai_tip_sync,
                now=now,
                status=status,
                hints=list(hints),
                rag_context=list(rag_context),
            )
        if self._tutorial_last_tip_text:
            return self._tutorial_last_tip_text
        return "KI-Schlange analysiert UI-Delta…"

    def _tutorial_async_enabled(self) -> bool:
        enabled = str(os.environ.get("ANANTA_TUI_SNAKE_AI_ASYNC", "1")).strip().lower() in {"1", "true", "yes", "on"}
        return bool(enabled and getattr(self._app, "is_running", False))

    def _poll_tutorial_async_tip_result(self) -> None:
        future = self._tutorial_async_tip_future
        if future is None or not future.done():
            return
        result = future.result()
        self._tutorial_async_tip_future = None
        if not isinstance(result, dict):
            return
        text = str(result.get("text") or "").strip()
        if not text:
            return
        self._tutorial_last_tip_text = text
        self._tutorial_last_source = str(result.get("source") or self._tutorial_last_source)
        self._tutorial_last_target = str(result.get("target") or self._tutorial_last_target)

    def _tutorial_status_delta_summary(self) -> str:
        mode = self.state.mode.value
        focus = self.state.focus.value
        section = self.state.section_id
        selected = self.state.selected_index
        snapshot = {
            "mode": str(mode),
            "focus": str(focus),
            "section": str(section),
            "idx": str(selected),
            "state": str((self.state.panel_states or {}).get(section, "")),
        }
        previous = dict(self._tutorial_status_snapshot)
        changed = [f"{key}={value}" for key, value in snapshot.items() if previous.get(key) != value]
        self._tutorial_status_snapshot = snapshot
        if not previous:
            return f"TUI state mode={mode} focus={focus} section={section} idx={selected}."
        if not changed:
            return "TUI delta: unchanged."
        return "TUI delta: " + ", ".join(changed)

    def _tutorial_ai_tip_sync(
        self,
        *,
        now: float,
        status: str,
        hints: list[str],
        rag_context: list[str],
    ) -> dict[str, str]:
        game = dict(self.state.header_logo_game or {})
        user_feed = str(game.get("tutorial_user_feed") or game.get("message") or "").strip()
        contact_zone = str(game.get("tutorial_ai_contact_zone") or "").strip()
        artifact_overlay = self._artifact_chat_prompt_overlay(game=game)
        priority = "explain-current-position" if bool(game.get("tutorial_ai_local_contact")) else "navigation-guidance"
        template = self._resolve_tutorial_prompt_template(game)
        overlay = self._render_tutorial_prompt_overlay(
            template=template,
            priority=priority,
            user_feed=user_feed or "(none)",
            contact_zone=contact_zone or "(none)",
        )
        effective_status = f"{status}\n{overlay}\n{artifact_overlay}"
        worker_tip = self._tutorial_ai_worker_propose_message(now=now, status=effective_status, hints=hints, rag_context=rag_context)
        if worker_tip:
            self._append_artifact_chat_ai_message(game=game, now=now, text=worker_tip)
            self.state = self.state.with_updates(header_logo_game=game)
            return {
                "source": "worker-propose",
                "target": self._tutorial_worker_target_hint or "follow",
                "text": worker_tip,
            }
        llm_hints = [*hints[:12], *[f"RAG {entry}" for entry in rag_context[:8]]]
        llm_tip = self._tutorial_ai_llm_message(now=now, status=effective_status, hints=llm_hints)
        if llm_tip:
            self._append_artifact_chat_ai_message(game=game, now=now, text=llm_tip)
            self.state = self.state.with_updates(header_logo_game=game)
            return {
                "source": "openai-compatible",
                "target": self._tutorial_worker_target_hint or "content",
                "text": llm_tip,
            }
        if not hints and not rag_context:
            base = _TUTORIAL_AI_KNOWLEDGE[int(now * 0.5) % len(_TUTORIAL_AI_KNOWLEDGE)]
            return {
                "source": "local-knowledge",
                "target": "follow",
                "text": f"{status} {base}",
            }
        cc = hints[int(now * 0.7) % len(hints)] if hints else ""
        rag = rag_context[int(now * 0.9) % len(rag_context)] if rag_context else ""
        parts = [status]
        if cc:
            parts.append(f"CodeCompass: {cc}")
        if rag:
            parts.append(f"RAG: {rag}")
        return {
            "source": "codecompass-rag",
            "target": "content",
            "text": " ".join(parts),
        }

    def _artifact_chat_prompt_overlay(self, *, game: dict[str, object]) -> str:
        target = game.get("artifact_intent_target")
        if not isinstance(target, dict):
            return "artifact_context=none"
        label = str(target.get("label") or "(unnamed)")
        payload = target.get("payload")
        path = ""
        if isinstance(payload, dict):
            path = str(payload.get("path") or "")
        excerpt = ""
        if path:
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if p.exists() and p.is_file():
                try:
                    lines = p.read_text(encoding="utf-8").splitlines()[:8]
                    excerpt = " | ".join(" ".join(line.split()) for line in lines if line.strip())[:420]
                except OSError:
                    excerpt = ""
                except UnicodeDecodeError:
                    excerpt = ""
        if excerpt:
            return f"artifact_context={label} path={path} excerpt={excerpt}"
        return f"artifact_context={label} path={path or '(none)'}"

    def _resolve_tutorial_prompt_template(self, game: dict[str, object]) -> str:
        env_template = str(os.environ.get("ANANTA_TUI_SNAKE_AI_PROMPT_TEMPLATE") or "").strip()
        game_template = str(game.get("tutorial_prompt_template") or "").strip()
        template = game_template or env_template or _TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT
        return template[:1200]

    def _render_tutorial_prompt_overlay(
        self,
        *,
        template: str,
        priority: str,
        user_feed: str,
        contact_zone: str,
    ) -> str:
        values = {
            "priority": str(priority or ""),
            "user_feed": str(user_feed or ""),
            "contact_zone": str(contact_zone or ""),
        }
        class _SafeTemplateDict(dict[str, str]):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"
        try:
            rendered = str(template).format_map(_SafeTemplateDict(values))
        except Exception:
            rendered = _TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT.format_map(_SafeTemplateDict(values))
        return " ".join(rendered.split())[:1200]

    def _tutorial_ai_worker_propose_message(
        self,
        *,
        now: float,
        status: str,
        hints: list[str],
        rag_context: list[str],
    ) -> str | None:
        backend = str(os.environ.get("ANANTA_TUI_SNAKE_AI_BACKEND", "")).strip().lower()
        if backend not in {"worker-propose", "worker", "opencode", "hermes"}:
            return None
        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", "8.0"))))
        cached_at, cached_msg = self._tutorial_worker_cache
        if cached_msg and (now - cached_at) < refresh_seconds:
            self._tutorial_last_source = "worker-propose"
            if self._tutorial_worker_target_hint:
                self._tutorial_last_target = self._tutorial_worker_target_hint
            return cached_msg

        base_url = str(self.state.endpoint or os.environ.get("ANANTA_BASE_URL") or "http://localhost:5000").strip()
        if not base_url:
            return None
        timeout_seconds = max(0.3, min(12.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "1.6"))))
        model = str(os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL", "")).strip()
        provider = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_PROVIDER", "")).strip()
        if not provider and backend in {"opencode", "hermes"}:
            provider = backend

        hint_block = "\n".join(f"- {h}" for h in hints[:8]) if hints else "- no codecompass hints"
        rag_block = "\n".join(f"- {h}" for h in rag_context[:8]) if rag_context else "- no rag_helper context"
        prompt = (
            f"{status}\n"
            "You are the tutorial snake controller for Ananta TUI.\n"
            "Use provided context hints when present.\n"
            "Return exactly one line <=180 chars with immediate guidance.\n"
            "Prefix the line with one steering tag in this format: [target=header|nav|content|detail|follow].\n"
            f"CodeCompass hints:\n{hint_block}\n"
            f"rag_helper context:\n{rag_block}\n"
        )
        payload: dict[str, object] = {"prompt": prompt, "temperature": 0.2}
        if model:
            payload["model"] = model
        if provider:
            payload["provider"] = provider
        strategy_mode = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_STRATEGY", "")).strip()
        if strategy_mode:
            payload["strategy_mode"] = strategy_mode
        token = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_TOKEN", "")).strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url=base_url.rstrip("/") + "/step/propose",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        data = parsed.get("data") if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) else parsed
        if not isinstance(data, dict):
            return None
        text = str(data.get("reason") or data.get("raw") or "").strip()
        if not text:
            return None
        single_line = " ".join(text.split())
        if not single_line:
            return None
        target_hint = ""
        match = re.search(r"\[target=(header|nav|content|detail|follow)\]", single_line, flags=re.IGNORECASE)
        if match:
            target_hint = match.group(1).lower()
            single_line = re.sub(r"\[target=(header|nav|content|detail|follow)\]\s*", "", single_line, flags=re.IGNORECASE)
        self._tutorial_worker_target_hint = target_hint
        self._tutorial_last_source = "worker-propose"
        self._tutorial_last_target = target_hint or "follow"
        clipped = single_line[:180].strip()
        if not clipped:
            return None
        self._tutorial_worker_cache = (now, clipped)
        return clipped

    def _tutorial_ai_llm_message(self, *, now: float, status: str, hints: list[str]) -> str | None:
        api_base, model, api_token = self._get_llm_api_config()
        if not model:
            model = "ananta-smoke"
        if not api_base:
            return None
        if not (model and api_base):
            return None
        parsed_api_base = urlparse(api_base)
        if (not parsed_api_base.path or parsed_api_base.path == "/") and parsed_api_base.netloc.endswith(":1234"):
            api_base = api_base.rstrip("/") + "/v1"

        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", "8.0"))))
        cached_at, cached_msg = self._tutorial_llm_cache
        if cached_msg and (now - cached_at) < refresh_seconds:
            self._tutorial_last_source = "openai-compatible"
            self._tutorial_last_target = "content"
            return cached_msg

        timeout_seconds = max(0.3, min(10.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "1.6"))))
        profile = self._resolve_tutorial_llm_profile(
            now=now,
            model=model,
            api_base=api_base,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        hint_block = "\n".join(f"- {h}" for h in hints[:8]) if hints else "- no codecompass hints available"
        prompt = (
            f"{status}\n"
            f"{str(profile.get('user_prompt') or '')}\n"
            f"CodeCompass + rag_helper hints:\n{hint_block}\n"
            "Max 180 chars."
        )
        content = self._tutorial_llm_chat_completion(
            model=model,
            api_base=api_base,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            system_prompt=str(profile.get("system_prompt") or "You are a concise in-product tutorial assistant."),
            user_prompt=prompt,
            temperature=float(profile.get("temperature") or 0.15),
            max_tokens=int(profile.get("max_tokens") or 72),
        )
        if not content:
            return None
        parsed = self._parse_tutorial_ai_llm_content(content)
        if not parsed:
            return None
        clipped, target_hint = parsed
        self._tutorial_worker_target_hint = target_hint
        self._tutorial_last_source = "openai-compatible"
        self._tutorial_last_target = target_hint or "content"
        self._tutorial_llm_cache = (now, clipped)
        return clipped

    def _resolve_tutorial_llm_profile(
        self,
        *,
        now: float,
        model: str,
        api_base: str,
        api_token: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        profile_key = f"{model}@{api_base}"
        if self._tutorial_llm_profile_cache and self._tutorial_llm_profile_key == profile_key:
            return dict(self._tutorial_llm_profile_cache)

        default_profile: dict[str, Any] = {
            "id": "compact-plain",
            "system_prompt": "You are a concise in-product tutorial assistant.",
            "user_prompt": "Provide one concise tutorial line for a snake assistant in this TUI. Focus on the immediate next action.",
            "temperature": 0.15,
            "max_tokens": 72,
        }
        training_enabled = str(os.environ.get("ANANTA_TUI_SNAKE_AI_TRAINING", "0")).strip().lower() in {"1", "true", "yes", "on"}
        if not training_enabled:
            self._tutorial_llm_profile_key = profile_key
            self._tutorial_llm_profile_cache = dict(default_profile)
            return dict(default_profile)

        candidates: list[dict[str, Any]] = [
            dict(default_profile),
            {
                "id": "compact-tagged",
                "system_prompt": "You are a concise in-product tutorial assistant.",
                "user_prompt": (
                    "Return exactly one short line with one steering prefix "
                    "[target=header|nav|content|detail|follow] and immediate next action."
                ),
                "temperature": 0.1,
                "max_tokens": 64,
            },
        ]

        best_profile: dict[str, Any] = dict(default_profile)
        best_score: tuple[int, float] = (-1, 999.0)
        for candidate in candidates:
            probe_prompt = (
                "TUI mode=normal focus=content section=dashboard idx=0.\n"
                f"{str(candidate.get('user_prompt') or '')}\n"
                "CodeCompass + rag_helper hints:\n- queue depth\n- tasks pending\n"
                "Max 180 chars."
            )
            started = time.monotonic()
            content = self._tutorial_llm_chat_completion(
                model=model,
                api_base=api_base,
                api_token=api_token,
                timeout_seconds=min(1.8, timeout_seconds),
                system_prompt=str(candidate.get("system_prompt") or ""),
                user_prompt=probe_prompt,
                temperature=float(candidate.get("temperature") or 0.15),
                max_tokens=int(candidate.get("max_tokens") or 72),
            )
            elapsed = time.monotonic() - started
            parsed = self._parse_tutorial_ai_llm_content(content or "")
            if not parsed:
                continue
            _, target_hint = parsed
            structure_bonus = 1 if target_hint else 0
            score = (1 + structure_bonus, elapsed)
            if score[0] > best_score[0] or (score[0] == best_score[0] and score[1] < best_score[1]):
                best_score = score
                best_profile = dict(candidate)

        self._tutorial_llm_profile_key = profile_key
        self._tutorial_llm_profile_cache = dict(best_profile)
        return dict(best_profile)

    def _tutorial_llm_chat_completion(
        self,
        *,
        model: str,
        api_base: str,
        api_token: str,
        timeout_seconds: float,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str | None:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": max(0.0, min(1.0, float(temperature))),
            "max_tokens": max(24, min(160, int(max_tokens))),
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        request = urllib.request.Request(
            url=api_base.rstrip("/") + "/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        if not isinstance(message, dict):
            return None
        content = str(message.get("content") or "").strip()
        return content or None

    def _parse_tutorial_ai_llm_content(self, content: str) -> tuple[str, str] | None:
        single_line = " ".join(str(content or "").split())
        if not single_line:
            return None
        target_hint = ""
        match = re.search(r"\[target=(header|nav|content|detail|follow)\]", single_line, flags=re.IGNORECASE)
        if match:
            target_hint = match.group(1).lower()
            single_line = re.sub(r"\[target=(header|nav|content|detail|follow)\]\s*", "", single_line, flags=re.IGNORECASE)
        elif single_line.startswith("{") and single_line.endswith("}"):
            try:
                payload = json.loads(single_line)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                text = str(payload.get("text") or payload.get("message") or "").strip()
                target = str(payload.get("target") or "").strip().lower()
                if target in {"header", "nav", "content", "detail", "follow"}:
                    target_hint = target
                if text:
                    single_line = " ".join(text.split())
        clipped = single_line[:180].strip()
        if not clipped:
            return None
        return clipped, target_hint

    def _get_llm_api_config(self) -> tuple[str, str, str]:
        game = self.state.header_logo_game if isinstance(getattr(self.state, "header_logo_game", None), dict) else {}
        backend_hint = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_BACKEND")
            or game.get("chat_backend")
            or ""
        ).strip().lower()
        raw_api_base = str(
            game.get("chat_backend_api_base")
            or os.environ.get("ANANTA_TUI_CHAT_API_BASE_URL")
            or os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
            or "http://192.168.178.100:1234/v1"
        ).strip()
        forced_defaults = (not raw_api_base) or ("lmstudio.test" in raw_api_base)
        api_base = raw_api_base
        if forced_defaults:
            api_base = "http://192.168.178.100:1234/v1"
        if backend_hint == "worker-propose":
            model = str(
                os.environ.get("ANANTA_TUI_CHAT_MODEL")
                or os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL")
                or "google/gemma-4-e4b"
            ).strip()
        else:
            model = str(
                (None if forced_defaults else game.get("chat_backend_model"))
                or os.environ.get("ANANTA_TUI_CHAT_MODEL")
                or os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL")
                or "google/gemma-4-e4b"
            ).strip()
        api_token = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_API_TOKEN")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
        return api_base, model, api_token

    def _llm_health_check_sync(self) -> dict:
        checked_at = time.time()
        api_base, model, api_token = self._get_llm_api_config()
        if not api_base:
            return {"reachable": False, "model": model, "last_check_at": checked_at, "error": "ANANTA_TUI_SNAKE_AI_API_BASE_URL nicht gesetzt"}
        try:
            url = f"{api_base.rstrip('/')}/models"
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_token:
                headers["Authorization"] = f"Bearer {api_token}"
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode())
                models_list = data.get("data") or []
                loaded = models_list[0].get("id", model) if models_list else model
                return {"reachable": True, "model": str(loaded), "last_check_at": checked_at, "error": ""}
        except TimeoutError:
            return {"reachable": False, "model": model, "last_check_at": checked_at, "error": "timeout"}
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            err = "timeout" if isinstance(reason, TimeoutError) else str(reason)[:80]
            return {"reachable": False, "model": model, "last_check_at": checked_at, "error": err}
        except Exception as exc:
            return {"reachable": False, "model": model, "last_check_at": checked_at, "error": str(exc)[:80]}

    def _maybe_tick_llm_health(self, game: dict, now: float) -> None:
        raw_interval = str(os.environ.get("ANANTA_TUI_LLM_HEALTH_INTERVAL_SECS", "30")).strip()
        interval = max(0.0, float(raw_interval)) if raw_interval.replace(".", "").isdigit() else 30.0
        if interval == 0:
            return
        last_at = float(game.get("llm_health_last_at") or 0)
        future = getattr(self, "_llm_health_future", None)
        if future is not None and future.done():
            try:
                result = future.result()
                game["llm_status"] = dict(result)
            except Exception:
                pass
            self._llm_health_future = None
            future = None
        if future is None and (now - last_at) >= interval:
            game["llm_health_last_at"] = now
            self._llm_health_future = self._get_snake_bg_executor().submit(
                self._llm_health_check_sync
            )
