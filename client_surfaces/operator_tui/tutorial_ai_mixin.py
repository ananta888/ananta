"""TutorialAiMixin — tutorial AI snake and LLM methods extracted from InteractiveOperatorTui.

Contains: _update_tutorial_ai_snake, _tutorial_target_label, _record_tutorial_propose_event,
          _tutorial_ai_target_cell, _step_toward_cell, _tutorial_ai_tip, _tutorial_async_enabled,
          _poll_tutorial_async_tip_result, _tutorial_status_delta_summary, _tutorial_ai_tip_sync,
          _artifact_chat_prompt_overlay, _resolve_tutorial_prompt_template,
          _render_tutorial_prompt_overlay, _tutorial_ai_worker_propose_message,
          _tutorial_ai_llm_message, _resolve_tutorial_llm_profile, _tutorial_llm_chat_completion,
          _parse_tutorial_ai_llm_content, _load_codecompass_hints, _load_rag_helper_context,
          _tutorial_relevance_tokens, _tutorial_context_relevance_score,
          _resolve_codecompass_output_dir, _ensure_codecompass_output_build_started,
          _poll_codecompass_output_build, _build_codecompass_outputs_sync
"""
from __future__ import annotations

import json
import os
import re
import math
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.keybindings_config import display_for_action

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
    # R04: embedding_text is the most informative field, so score it above
    # generic compact text while keeping keyword fallback active.
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


def _load_codecompass_hints_from_dir(out_dir: Path) -> list[str]:
    """Blocking load — always called from background thread."""
    try:
        from worker.retrieval.codecompass_output_reader import CodeCompassOutputReader
        payload = CodeCompassOutputReader().load_from_output_dir(output_dir=out_dir)
    except Exception:
        return []
    records = payload.get("records") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        return []
    hints: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        kind = str(record.get("kind") or record.get("type") or "").strip()
        file_path = str(record.get("file") or record.get("path") or "").strip()
        name = str(record.get("name") or record.get("id") or "").strip()
        if not (kind or file_path or name):
            continue
        parts = [p for p in [kind, name, file_path] if p]
        hint = " · ".join(parts)
        if hint:
            hints.append(hint)
        if len(hints) >= 64:
            break
    return hints


def _load_rag_context_from_dir(
    out_dir: Path,
    query_tokens: list[str],
    top_k: int,
    max_records_per_file: int,
    scope_filter: str = "tui_only",
) -> list[str]:
    """Blocking RAG context build — always called from background thread.

    scope_filter: 'tui_only' restricts graph edges to operator_tui (ambient tip default),
                  'full' includes all files (chat default — R06).
    """
    manifest_path = out_dir / "manifest.json"
    files: list[str] = []
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
                            files.append(rel)
    files.extend([
        "context.jsonl", "details.jsonl", "index.jsonl", "xml_overview.jsonl",
        "embedding.jsonl", "graph_nodes.jsonl", "graph_edges.jsonl", "relations.jsonl",
    ])
    deduped: list[str] = []
    seen: set[str] = set()
    for rel in files:
        norm = rel.strip().lstrip("/")
        if norm and norm not in seen:
            seen.add(norm)
            deduped.append(norm)

    scope_full = scope_filter == "full" or str(
        os.environ.get("ANANTA_TUI_RAG_SCOPE_FILTER", "")
    ).strip().lower() == "full"

    use_embedding_api = str(os.environ.get("ANANTA_TUI_CHAT_USE_EMBEDDING_API", "")).strip().lower() in {"1", "true", "yes", "on"}
    query_embedding = _embedding_vector_for_text(" ".join(query_tokens)) if use_embedding_api and query_tokens else []

    candidates: list[tuple[float, str]] = []
    embedding_api_calls = 0
    for rel in deduped[:24]:
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
            # R06: scope filter — tui_only restricts graph edges to operator_tui
            if not scope_full and source_kind in {"graph_nodes", "graph_edges"} and source_file:
                sl = source_file.lower()
                if "client_surfaces/operator_tui" not in sl and "/operator_tui/" not in sl:
                    tgt = str(parsed.get("target_file") or parsed.get("target_path") or "").lower()
                    if "client_surfaces/operator_tui" not in tgt and "/operator_tui/" not in tgt:
                        continue
            # R04: extract embedding_text separately for boosted scoring
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
            # R04: use boosted scorer that weights embedding_text above generic text.
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
            candidates.append((score, compact[:240]))

    ranked = sorted(candidates, key=lambda item: item[0], reverse=True)
    results = [item[1] for item in ranked[:top_k]]

    # R03: name-lookup in details.jsonl for function/class tokens (no position limit)
    name_hits = _name_lookup_from_details(out_dir, query_tokens, already_found=set(results))
    return name_hits + results if name_hits else results


def _name_lookup_from_details(
    out_dir: Path,
    query_tokens: list[str],
    already_found: set[str],
) -> list[str]:
    """R03: Direct name-match in details.jsonl for function/class tokens.

    Scans without a position limit so deeply-nested functions are always found.
    Only runs when a token looks like a Python identifier (_foo, FooBar, foo_bar).
    """
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


class TutorialAiMixin:
    """Mixin providing tutorial AI snake movement and LLM tip generation."""

    def _update_tutorial_ai_snake(
        self,
        game: dict[str, object],
        snakes: dict[str, dict[str, object]],
        *,
        now: float,
        board_w: int,
        board_h: int,
        enabled: bool,
    ) -> None:
        sid = "s-ai"
        if not enabled:
            snakes.pop(sid, None)
            return
        hints = self._load_codecompass_hints(now=now)
        rag_context = self._load_rag_helper_context(now=now)
        context_tokens = [*hints[:10], *rag_context[:10]]
        intent_confidence = str(game.get("artifact_intent_confidence") or "none")
        artifact_target = game.get("artifact_intent_target")
        # Respect heuristic-selected target mode; artifact intent can escalate to fast_target
        heuristic_mode = str(game.get("tutorial_ai_target_mode") or "follow_user")
        target_mode = heuristic_mode
        if intent_confidence in {"likely", "confirmed"} and heuristic_mode != "lurk":
            target_mode = "fast_target"
            if isinstance(artifact_target, dict):
                context_tokens.insert(0, f"artifact:{artifact_target.get('label')}")
                context_tokens.insert(0, f"target:{artifact_target.get('pane') or 'content'}")
        if self._tutorial_worker_target_hint:
            context_tokens.insert(0, f"target:{self._tutorial_worker_target_hint}")
        local = snakes.get(str(self.state.header_logo_game.get("local_snake_id", "s1"))) if isinstance(self.state.header_logo_game, dict) else None
        local_head = None
        if isinstance(local, dict):
            local_snake = local.get("snake")
            if isinstance(local_snake, list) and local_snake:
                head = local_snake[0]
                if isinstance(head, (list, tuple)) and len(head) == 2:
                    local_head = (int(head[0]) % max(1, board_w), int(head[1]) % max(1, board_h))
        # For lurk mode: target a cell offset from user (keep distance)
        follow_distance = max(2, int(game.get("ai_snake_follow_distance") or 4))
        if target_mode == "lurk" and local_head is not None:
            lx, ly = local_head
            target = ((lx + follow_distance) % max(1, board_w), (ly + follow_distance // 2) % max(1, board_h))
        else:
            target = self._tutorial_ai_target_cell(
                board_w=board_w,
                board_h=board_h,
                context_tokens=context_tokens,
                local_head=local_head,
            )
        artifact_cell = game.get("artifact_target_cell")
        if target_mode == "fast_target" and isinstance(artifact_cell, (list, tuple)) and len(artifact_cell) == 2:
            target = (int(artifact_cell[0]) % max(1, board_w), int(artifact_cell[1]) % max(1, board_h))
        existing = snakes.get(sid, {})
        existing_snake_raw = existing.get("snake") if isinstance(existing, dict) else []
        existing_snake = [
            (int(p[0]) % max(1, board_w), int(p[1]) % max(1, board_h))
            for p in (existing_snake_raw or [])
            if isinstance(p, (list, tuple)) and len(p) == 2
        ]
        if existing_snake:
            start_head = existing_snake[0]
        else:
            start_head = ((target[0] - 1) % max(1, board_w), target[1] % max(1, board_h))
            existing_snake = [start_head]
        new_head = self._step_toward_cell(
            current=start_head,
            target=target,
            board_w=board_w,
            board_h=board_h,
        )
        if target_mode == "fast_target":
            new_head = self._step_toward_cell(
                current=new_head,
                target=target,
                board_w=board_w,
                board_h=board_h,
            )
        body = [new_head, *existing_snake]
        while len(body) < 10:
            tx = (body[-1][0] - 1) % max(1, board_w)
            body.append((tx, body[-1][1]))
        body = body[:10]
        trail = list(body)
        ai_local_contact = False
        contact_zone = ""
        if local_head is not None:
            ai_local_contact = abs(new_head[0] - local_head[0]) + abs(new_head[1] - local_head[1]) <= 1
            if ai_local_contact:
                contact_zone = self._tutorial_target_label(board_w=board_w, board_h=board_h, target=local_head)
        game["tutorial_ai_local_contact"] = ai_local_contact
        game["tutorial_ai_contact_zone"] = contact_zone
        game["tutorial_ai_contact_at"] = float(now) if ai_local_contact else 0.0
        game["tutorial_ai_target_mode"] = target_mode
        if target_mode == "fast_target":
            dist = abs(new_head[0] - target[0]) + abs(new_head[1] - target[1])
            if dist <= 1:
                game["tutorial_ai_target_mode"] = "explain_target"
                self._append_artifact_chat_ai_message(game=game, now=now, text="Ziel erreicht. Ich erkläre dieses Artefakt im Kontext.")

        tip = self._tutorial_ai_tip(now=now)
        target_label = self._tutorial_last_target or self._tutorial_target_label(board_w=board_w, board_h=board_h, target=target)
        source_label = self._tutorial_last_source or "codecompass-rag"
        self._record_tutorial_propose_event(
            game,
            now=now,
            source=source_label,
            target=target_label,
            text=tip,
        )
        existing_access = str(existing.get("access_level") or "view") if isinstance(existing, dict) else "view"
        snakes[sid] = {
            "id": sid,
            "pseudonym": "tutor-ai",
            "oidc_provider": "codecompass-ai",
            "snake": body,
            "trail_path": trail,
            "selection_cells": [],
            "message": tip,
            "message_style": "ticker",
            "snake_color": "amber",
            "trail_window": 28,
            "trail_speed": 8.0,
            "active": True,
            "updated_at": now,
            "local": False,
            "knowledge_scope": ("tui", "architecture", "workflow"),
            "target_cell": target,
            "mode": game.get("tutorial_ai_target_mode") or "follow_user",
            "access_level": existing_access,
        }

    def _tutorial_target_label(
        self,
        *,
        board_w: int,
        board_h: int,
        target: tuple[int, int],
    ) -> str:
        tx, ty = int(target[0]), int(target[1])
        if ty <= max(2, board_h // 6):
            return "header"
        if tx <= max(2, board_w // 4):
            return "nav"
        if tx >= max(2, board_w - max(8, board_w // 4)) and ty >= max(2, board_h - max(6, board_h // 4)):
            return "detail"
        return "content"

    def _record_tutorial_propose_event(
        self,
        game: dict[str, object],
        *,
        now: float,
        source: str,
        target: str,
        text: str,
    ) -> None:
        history_raw = game.get("tutorial_propose_history")
        history: list[dict[str, object]]
        if isinstance(history_raw, list):
            history = [dict(entry) for entry in history_raw if isinstance(entry, dict)]
        else:
            history = []
        entry = {
            "at": float(now),
            "source": str(source or "unknown"),
            "target": str(target or "content"),
            "text": str(text or "").strip(),
        }
        if not entry["text"]:
            return
        last = history[-1] if history else None
        if isinstance(last, dict):
            if (
                str(last.get("source") or "") == str(entry["source"])
                and str(last.get("target") or "") == str(entry["target"])
                and str(last.get("text") or "") == str(entry["text"])
            ):
                return
        history.append(entry)
        game["tutorial_propose_history"] = history[-8:]

    def _tutorial_ai_target_cell(
        self,
        *,
        board_w: int,
        board_h: int,
        context_tokens: list[str],
        local_head: tuple[int, int] | None,
    ) -> tuple[int, int]:
        text = " ".join(context_tokens).lower()
        if "target:header" in text:
            return (max(0, board_w - max(4, board_w // 6)), max(1, board_h // 6))
        if "target:nav" in text:
            return (max(1, board_w // 5), max(2, board_h // 2))
        if "target:content" in text:
            return (max(2, board_w // 2), max(2, board_h // 2))
        if "target:detail" in text:
            return (max(2, board_w - max(8, board_w // 4)), max(2, board_h - max(4, board_h // 4)))
        if "target:follow" in text and local_head is not None:
            return ((local_head[0] + 3) % max(1, board_w), local_head[1] % max(1, board_h))
        if any(token in text for token in ("endpoint", "auth", "header", "config", "oidc")):
            return (max(0, board_w - max(4, board_w // 6)), max(1, board_h // 6))
        if any(token in text for token in ("task", "goal", "section", "navigation", "queue")):
            return (max(1, board_w // 5), max(2, board_h // 2))
        if any(token in text for token in ("detail", "inspect", "artifact", "context", "result")):
            return (max(2, board_w - max(8, board_w // 4)), max(2, board_h - max(4, board_h // 4)))
        if local_head is not None:
            return ((local_head[0] + 3) % max(1, board_w), local_head[1] % max(1, board_h))
        return (max(2, board_w // 2), max(2, board_h // 2))

    def _step_toward_cell(
        self,
        *,
        current: tuple[int, int],
        target: tuple[int, int],
        board_w: int,
        board_h: int,
    ) -> tuple[int, int]:
        cx, cy = int(current[0]), int(current[1])
        tx, ty = int(target[0]), int(target[1])
        bw = max(1, int(board_w))
        bh = max(1, int(board_h))
        raw_dx = (tx % bw) - (cx % bw)
        raw_dy = (ty % bh) - (cy % bh)
        dx = raw_dx
        dy = raw_dy
        if abs(raw_dx) > bw / 2:
            dx = raw_dx - bw if raw_dx > 0 else raw_dx + bw
        if abs(raw_dy) > bh / 2:
            dy = raw_dy - bh if raw_dy > 0 else raw_dy + bh
        if abs(dx) >= abs(dy) and dx != 0:
            step_x = 1 if dx > 0 else -1
            return ((cx + step_x) % bw, cy % bh)
        if dy != 0:
            step_y = 1 if dy > 0 else -1
            return (cx % bw, (cy + step_y) % bh)
        return (cx % bw, cy % bh)

    def _tutorial_ai_tip(self, *, now: float) -> str:
        status = self._tutorial_status_delta_summary()
        hints = self._load_codecompass_hints(now=now)
        rag_context = self._load_rag_helper_context(now=now)
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
            "Use CodeCompass and rag_helper context.\n"
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
        # L04: unified config
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

    def _load_codecompass_hints(self, *, now: float) -> list[str]:
        """Return codecompass hints without blocking the main loop.

        Disk I/O (CodeCompassOutputReader) runs in a background thread.
        Returns stale cache while loading; updates cache when future completes.
        """
        cached_at, cached = self._tutorial_codecompass_cache

        pending = getattr(self, "_codecompass_hints_future", None)
        if pending is not None and pending.done():
            try:
                cached = pending.result()
                self._tutorial_codecompass_cache = (now, cached)
            except Exception:
                pass
            self._codecompass_hints_future = None
            pending = None

        if cached and (now - cached_at) < 6.0:
            return cached

        if pending is None:
            out_dir = self._resolve_codecompass_output_dir()
            if out_dir is None:
                self._tutorial_codecompass_cache = (now, [])
                return []
            self._codecompass_hints_future = self._get_snake_bg_executor().submit(
                _load_codecompass_hints_from_dir, out_dir
            )

        return cached

    def _load_rag_helper_context(self, *, now: float) -> list[str]:
        """Return RAG context without blocking the main loop.

        File reads and scoring run in a background thread.
        query_tokens are captured on the main thread before submitting.
        """
        cached_at, cached = self._tutorial_rag_cache

        pending = getattr(self, "_rag_context_future", None)
        if pending is not None and pending.done():
            try:
                cached = pending.result()
                self._tutorial_rag_cache = (now, cached)
            except Exception:
                pass
            self._rag_context_future = None
            pending = None

        if cached and (now - cached_at) < 6.0:
            return cached

        if pending is None:
            out_dir = self._resolve_codecompass_output_dir()
            if out_dir is None:
                self._tutorial_rag_cache = (now, [])
                return []
            query_tokens = self._tutorial_relevance_tokens()   # main thread, uses self.state
            top_k = max(12, min(96, int(os.environ.get("ANANTA_TUI_SNAKE_RAG_TOP_K", "48"))))
            max_recs = max(80, min(3000, int(os.environ.get("ANANTA_TUI_SNAKE_RAG_MAX_RECORDS_PER_FILE", "800"))))
            self._rag_context_future = self._get_snake_bg_executor().submit(
                _load_rag_context_from_dir, out_dir, query_tokens, top_k, max_recs
            )

        return cached

    def _get_snake_bg_executor(self) -> ThreadPoolExecutor:
        executor: ThreadPoolExecutor | None = getattr(self, "_snake_bg_executor", None)
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tui-snake-io")
            self._snake_bg_executor = executor
        return executor

    def _tutorial_relevance_tokens(self) -> list[str]:
        game = dict(self.state.header_logo_game or {})
        raw_parts = [
            str(self.state.section_id or ""),
            str(self.state.focus.value or ""),
            str(self.state.mode.value or ""),
            str(game.get("tutorial_user_feed") or ""),
            str(game.get("tutorial_ai_contact_zone") or ""),
            "ananta tui snake operator_tui interactive renderer prompt propose",
        ]
        text = " ".join(raw_parts).lower()
        return [token for token in re.findall(r"[a-z0-9_./-]+", text) if len(token) >= 2][:64]

    def _tutorial_context_relevance_score(self, text: str, *, query_tokens: list[str]) -> float:
        return _score_rag_record(text, query_tokens)

    def _resolve_codecompass_output_dir(self) -> Path | None:
        self._poll_codecompass_output_build()
        candidates = [
            os.environ.get("ANANTA_TUI_CODECOMPASS_OUTPUT_DIR"),
            os.environ.get("CODECOMPASS_OUTPUT_DIR"),
            os.environ.get("ANANTA_CODECOMPASS_OUTPUT_DIR"),
            "rag-helper/out",
            "rag-helper/output",
            "codecompass-out",
        ]
        if self._codecompass_build_output_dir is not None:
            candidates.insert(0, str(self._codecompass_build_output_dir))
        for raw in candidates:
            if not raw:
                continue
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            if path.exists() and path.is_dir() and (path / "index.jsonl").exists():
                self._maybe_schedule_codecompass_rebuild(path)
                return path
        self._ensure_codecompass_output_build_started()
        return None

    def _maybe_schedule_codecompass_rebuild(self, out_dir: Path) -> None:
        auto_enabled = str(os.environ.get("ANANTA_TUI_AUTO_BUILD_CODECOMPASS", "1")).strip().lower() in {"1", "true", "yes", "on"}
        game = dict(self.state.header_logo_game or {})
        if not auto_enabled:
            game["codecompass_build_status"] = "ready"
            self.state = self.state.with_updates(header_logo_game=game)
            return
        if self._codecompass_build_future is not None and not self._codecompass_build_future.done():
            game["codecompass_build_status"] = "building"
            self.state = self.state.with_updates(header_logo_game=game)
            return
        if self._codecompass_index_is_stale(out_dir):
            game["codecompass_build_status"] = "stale"
            self._tutorial_codecompass_cache = (0.0, [])
            self._tutorial_rag_cache = (0.0, [])
            self._codecompass_build_future = self._codecompass_build_executor.submit(self._build_codecompass_outputs_sync)
            game["codecompass_build_status"] = "building"
        else:
            game["codecompass_build_status"] = "ready"
        self.state = self.state.with_updates(header_logo_game=game)

    def _ensure_codecompass_output_build_started(self) -> None:
        auto_enabled = str(os.environ.get("ANANTA_TUI_AUTO_BUILD_CODECOMPASS", "1")).strip().lower() in {"1", "true", "yes", "on"}
        if not auto_enabled:
            return
        if self._codecompass_build_future is not None and not self._codecompass_build_future.done():
            return
        # R05: rebuild when existing index is stale. Scan candidates directly to avoid
        # recursive call through _resolve_codecompass_output_dir.
        if self._codecompass_build_output_dir is not None:
            out_dir: Path | None = self._codecompass_build_output_dir
        else:
            out_dir = None
            for raw in [
                os.environ.get("ANANTA_TUI_CODECOMPASS_OUTPUT_DIR"),
                os.environ.get("CODECOMPASS_OUTPUT_DIR"),
                "rag-helper/out",
                "rag-helper/output",
                "codecompass-out",
            ]:
                if not raw:
                    continue
                p = Path(raw).expanduser()
                if not p.is_absolute():
                    p = (Path.cwd() / p).resolve()
                if p.exists() and p.is_dir() and (p / "index.jsonl").exists():
                    out_dir = p
                    break
        game = dict(self.state.header_logo_game or {})
        if out_dir is not None and self._codecompass_index_is_stale(out_dir):
            self._tutorial_codecompass_cache = (0.0, [])
            self._tutorial_rag_cache = (0.0, [])
            game["codecompass_build_status"] = "stale"
        else:
            game["codecompass_build_status"] = "building"
        self.state = self.state.with_updates(header_logo_game=game)
        self._codecompass_build_future = self._codecompass_build_executor.submit(self._build_codecompass_outputs_sync)

    def _codecompass_index_is_stale(self, out_dir: Path) -> bool:
        """R05: returns True when .py files are newer than the manifest build_time."""
        min_age = max(60, int(os.environ.get("ANANTA_TUI_AUTO_REBUILD_MAX_AGE_SECONDS", "3600")))
        manifest_path = out_dir / "manifest.json"
        if not manifest_path.exists():
            return True
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            build_time = float(manifest.get("build_time") or manifest_path.stat().st_mtime)
        except Exception:
            return False
        if (time.time() - build_time) < min_age:
            return False
        # Check newest .py mtime in key dirs
        root = Path.cwd()
        newest_mtime = 0.0
        for check_dir in [root / "client_surfaces" / "operator_tui", root / "worker"]:
            if not check_dir.exists():
                continue
            for py_file in check_dir.rglob("*.py"):
                try:
                    newest_mtime = max(newest_mtime, py_file.stat().st_mtime)
                except OSError:
                    pass
        return newest_mtime > build_time

    def _poll_codecompass_output_build(self) -> Path | None:
        future = self._codecompass_build_future
        if future is None or not future.done():
            return None
        self._codecompass_build_future = None
        built = future.result()
        game = dict(self.state.header_logo_game or {})
        if built is None:
            game["codecompass_build_status"] = "stale"
            self.state = self.state.with_updates(header_logo_game=game)
            return None
        self._codecompass_build_output_dir = built
        self._tutorial_codecompass_cache = (0.0, [])
        self._tutorial_rag_cache = (0.0, [])
        game["codecompass_build_status"] = "ready"
        self.state = self.state.with_updates(header_logo_game=game)
        return built

    def _build_codecompass_outputs_sync(self) -> Path | None:
        root_dir = Path.cwd()
        candidate_scripts = [
            root_dir / "rag-helper" / "codecompass_rag.py",
            root_dir / "codecompass_rag.py",
        ]
        script_path = next((path for path in candidate_scripts if path.exists() and path.is_file()), None)
        if script_path is None:
            return None
        output_dir = (root_dir / "rag-helper" / "out").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            "python3",
            str(script_path),
            str(root_dir),
            "-o",
            str(output_dir),
            "--retrieval-output-mode",
            "both",
            "--graph-export-mode",
            "jsonl",
            "--relation-output-mode",
            "both",
            "--output-partition-mode",
            "by-kind",
        ]
        timeout_seconds = max(20, min(900, int(os.environ.get("ANANTA_TUI_CODECOMPASS_BUILD_TIMEOUT", "240"))))
        try:
            completed = subprocess.run(
                command,
                cwd=str(root_dir),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return output_dir if (output_dir / "index.jsonl").exists() else None

    # ── L04: unified LLM API config ───────────────────────────────────────────

    def _get_llm_api_config(self) -> tuple[str, str, str]:
        """L04: single source of truth for api_base, model, api_token."""
        api_base = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
            or "http://192.168.178.100:1234/v1"
        ).strip()
        model = str(os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL") or "google/gemma-4-e4b").strip()
        api_token = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_API_TOKEN")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
        return api_base, model, api_token

    # ── L01: LMStudio health check ────────────────────────────────────────────

    def _llm_health_check_sync(self) -> dict:
        """L01: blocking health check — runs in background thread."""
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
        """L01: schedule and poll health checks at configured interval."""
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
            self._llm_health_future = None  # type: ignore[attr-defined]
            future = None
        if future is None and (now - last_at) >= interval:
            game["llm_health_last_at"] = now
            self._llm_health_future = self._get_snake_bg_executor().submit(  # type: ignore[attr-defined]
                self._llm_health_check_sync
            )
        self.state = self.state.with_updates(header_logo_game=game)
