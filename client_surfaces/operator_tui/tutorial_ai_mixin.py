"""TutorialAiMixin — tutorial AI snake and LLM methods extracted from InteractiveOperatorTui.

State management, codecompass build orchestration, and coordination.
AI engine, LLM interaction, and step definitions in sub-modules.
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from client_runtime import process

from client_surfaces.operator_tui.tutorial_ai_engine import (
    TutorialAiEngineMixin,
    _load_codecompass_hints_from_dir,
    _load_rag_context_from_dir,
    _score_rag_record,
    _TUTORIAL_AI_KNOWLEDGE,
    _TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT,
)
from client_surfaces.operator_tui.tutorial_steps import (
    _step_toward_cell,
    _tutorial_ai_target_cell,
    _tutorial_target_label,
    _record_tutorial_propose_event,
)

if TYPE_CHECKING:
    from concurrent.futures import Future


class TutorialAiMixin(TutorialAiEngineMixin):
    """Mixin providing tutorial AI snake movement and state management."""

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
        follow_distance = max(2, int(game.get("ai_snake_follow_distance") or 4))
        if target_mode == "lurk" and local_head is not None:
            lx, ly = local_head
            target = ((lx + follow_distance) % max(1, board_w), (ly + follow_distance // 2) % max(1, board_h))
        else:
            target = _tutorial_ai_target_cell(
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
        new_head = _step_toward_cell(
            current=start_head,
            target=target,
            board_w=board_w,
            board_h=board_h,
        )
        if target_mode == "fast_target":
            new_head = _step_toward_cell(
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
                contact_zone = _tutorial_target_label(board_w=board_w, board_h=board_h, target=local_head)
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
        target_label = self._tutorial_last_target or _tutorial_target_label(board_w=board_w, board_h=board_h, target=target)
        source_label = self._tutorial_last_source or "codecompass-rag"
        _record_tutorial_propose_event(
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

    def _load_codecompass_hints(self, *, now: float) -> list[str]:
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
            query_tokens = self._tutorial_relevance_tokens()
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
            completed = process.run(
                command,
                cwd=str(root_dir),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, process.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return output_dir if (output_dir / "index.jsonl").exists() else None


# ── Backward-compatible re-exports ────────────────────────────────────────
# These symbols were previously available directly from this module.
# New code should import from the appropriate sub-module.

from client_surfaces.operator_tui.tutorial_ai_engine import (
    _cosine_similarity as _cosine_similarity,
    _embedding_vector_for_text as _embedding_vector_for_text,
    _load_codecompass_hints_from_dir as _load_codecompass_hints_from_dir,
    _load_rag_context_from_dir as _load_rag_context_from_dir,
    _name_lookup_from_details as _name_lookup_from_details,
    _score_rag_record as _score_rag_record,
    _score_rag_record_with_embedding as _score_rag_record_with_embedding,
    _TUTORIAL_AI_KNOWLEDGE as _TUTORIAL_AI_KNOWLEDGE,
    _TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT as _TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT,
)
from client_surfaces.operator_tui.tutorial_steps import (
    _record_tutorial_propose_event as _record_tutorial_propose_event,
    _step_toward_cell as _step_toward_cell,
    _tutorial_ai_target_cell as _tutorial_ai_target_cell,
    _tutorial_target_label as _tutorial_target_label,
)
