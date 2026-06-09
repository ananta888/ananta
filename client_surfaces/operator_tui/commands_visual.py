from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState

def _doc_preflight_report() -> dict[str, object]:
    def _which(name: str) -> str:
        return shutil.which(name) or ""

    def _exists(path: str) -> bool:
        try:
            return Path(path).expanduser().exists()
        except Exception:
            return False

    def _wsl2_detected() -> bool:
        if str(os.environ.get("ANANTA_TUI_WSL2") or "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
        if os.environ.get("WSL_DISTRO_NAME"):
            return True
        try:
            text = Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
            return "microsoft" in text or "wsl" in text
        except OSError:
            return False

    playwright_ok = False
    try:
        import importlib.util

        playwright_ok = importlib.util.find_spec("playwright") is not None
    except Exception:
        playwright_ok = False

    mermaid_js_candidates = (
        "node_modules/mermaid/dist/mermaid.min.js",
        "node_modules/.bin/../mermaid/dist/mermaid.min.js",
    )
    mermaid_js_path = next((p for p in mermaid_js_candidates if _exists(p)), "")

    return {
        "wsl2_detected": _wsl2_detected(),
        "term": str(os.environ.get("TERM") or ""),
        "term_program": str(os.environ.get("TERM_PROGRAM") or ""),
        "mmdc_path": _which("mmdc"),
        "node_path": _which("node"),
        "chafa_path": _which("chafa"),
        "playwright_installed": playwright_ok,
        "mermaid_js_path": mermaid_js_path,
    }


def _doc_preflight_hints(report: dict[str, object]) -> list[str]:
    hints: list[str] = []
    if not report.get("mmdc_path"):
        hints.append("install: npm install -g @mermaid-js/mermaid-cli")
    if not report.get("node_path"):
        hints.append("install: nodejs/npm required for mmdc")
    if not report.get("chafa_path"):
        hints.append("optional: sudo apt install -y chafa")
    if not report.get("playwright_installed"):
        hints.append("optional: pip install playwright && playwright install chromium")
    if not report.get("mermaid_js_path"):
        hints.append("optional: npm install mermaid (for playwright backend assets)")
    if report.get("wsl2_detected"):
        hints.append("wsl2: prefer mmdc + ansi/chafa; browser mode is not recommended for docs")
    if not hints:
        hints.append("ok: recommended markdown/mermaid dependencies available")
    return hints


def _doc_switch_markdown_from_state(state: OperatorState) -> tuple[str, dict[str, str]]:
    section_id = str(state.section_id or "dashboard")
    payloads = dict(state.section_payloads or {})
    payload = payloads.get(section_id)
    heading = f"# {section_id}\n\n"

    if isinstance(payload, dict):
        for key in ("markdown", "text", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return heading + value.strip() + "\n", {"kind": "state", "content_or_ref": section_id, "title": section_id}
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    elif isinstance(payload, list):
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    elif payload is None:
        body = "(keine Daten im aktuellen Bereich)"
    else:
        body = str(payload)

    markdown = f"{heading}```json\n{body}\n```\n"
    return markdown, {"kind": "state", "content_or_ref": section_id, "title": section_id}


def _apply_doc_mode(game: dict[str, object], mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m in {"simple", "plain"}:
        game["markdown_stream_plain"] = True
        game["markdown_mermaid_render_requested"] = False
        game["markdown_mermaid_config"] = {
            "markdown_mode": "ansi",
            "mermaid_mode": "disabled",
            "mermaid_renderers": ["fallback_codeblock"],
        }
        return "simple"
    if m in {"rendered", "markdown"}:
        game["markdown_stream_plain"] = False
        game["markdown_mermaid_render_requested"] = False
        game["markdown_mermaid_config"] = {
            "markdown_mode": "ansi",
            "mermaid_mode": "disabled",
            "mermaid_renderers": ["fallback_codeblock"],
        }
        return "rendered"
    game["markdown_stream_plain"] = False
    game["markdown_mermaid_render_requested"] = True
    game["markdown_mermaid_config"] = {
        "markdown_mode": "ansi",
        "mermaid_mode": "auto",
        "mermaid_renderers": ["mermaid_cli", "playwright", "fallback_codeblock"],
    }
    return "mermaid"



def handle_visual_commands(command: str, args: list[str], state: OperatorState) -> CommandResult:
    if command == "visual":
        game = dict(state.header_logo_game or {})
        action = str(args[0]).strip().lower() if args else "status"
        current_enabled = bool(game.get("visual_viewport_enabled"))
        if action in {"on", "off", "toggle"}:
            if action == "toggle":
                enabled = not current_enabled
            else:
                enabled = action == "on"
            game["visual_viewport_enabled"] = enabled
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    status_message=f"visual viewport: {'an' if enabled else 'aus'}",
                ),
                "visual toggled",
            )
        if action == "list":
            views = [str(item) for item in (game.get("visual_viewport_available_views") or []) if str(item).strip()]
            listed = ", ".join(views) if views else "(keine bekannt)"
            return CommandResult(
                state.with_updates(status_message=f"visual views: {listed}"),
                "visual views listed",
            )
        if action == "view":
            if len(args) < 2:
                return CommandResult(state, "visual view: id erforderlich", handled=False)
            target = str(args[1]).strip()
            if not target:
                return CommandResult(state, "visual view: id erforderlich", handled=False)
            available_views = [str(item) for item in (game.get("visual_viewport_available_views") or []) if str(item).strip()]
            if available_views and target not in available_views:
                listed = ", ".join(available_views)
                return CommandResult(
                    state.with_updates(status_message=f"visual view unbekannt: {target} | {listed}"),
                    "visual view unknown",
                    handled=False,
                )
            game["visual_viewport_active_view_request"] = target
            game["visual_viewport_enabled"] = True
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    status_message=f"visual view requested: {target}",
                ),
                "visual view requested",
            )
        if action == "status":
            runtime = dict(game.get("visual_runtime_status") or {})
            view = str(runtime.get("active_view") or game.get("visual_viewport_active_view") or "-")
            renderer = str(runtime.get("active_renderer") or "-")
            adapter = str(runtime.get("active_adapter") or "-")
            return CommandResult(
                state.with_updates(
                    status_message=(
                        f"visual: {'an' if current_enabled else 'aus'} "
                        f"view={view} renderer={renderer} adapter={adapter}"
                    )
                ),
                "visual status",
            )
        return CommandResult(state, "visual: on|off|toggle|status|list|view <id>", handled=False)
    if command in {"doc", "md", "markdown"}:
        sub = str(args[0]).strip().lower() if args else "help"
        if sub in {"help", "status"}:
            return CommandResult(
                state.with_updates(status_message="doc: open <path-to-md> | switch | mode <simple|rendered|mermaid> | preflight"),
                "doc help",
                handled=(sub == "status"),
            )
        if sub == "mode":
            if len(args) < 2:
                msg = "doc mode: simple|rendered|mermaid"
                return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
            game = dict(state.header_logo_game or {})
            selected = _apply_doc_mode(game, str(args[1]))
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    focus=FocusPane.CONTENT,
                    status_message=f"doc mode: {selected}",
                ),
                f"doc mode: {selected}",
            )
        if sub in {"switch", "here"}:
            game = dict(state.header_logo_game or {})
            viewport_cfg = dict(game.get("visual_viewport") or {})
            viewport_cfg["enabled"] = True
            game["visual_viewport"] = viewport_cfg
            game["center_browser_active"] = False
            game["center_browser_status"] = "exited"
            markdown, source = _doc_switch_markdown_from_state(state)
            game["visual_viewport_enabled"] = True
            game["visual_viewport_active_view_request"] = "markdown_mermaid_document"
            game["markdown_text"] = markdown
            _apply_doc_mode(game, "simple")
            game["center_window_view_mode"] = "simple"
            game["document_source"] = source
            game["_cmd_feedback"] = "doc_view: markdown_mermaid_document"
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    focus=FocusPane.CONTENT,
                    status_message="doc_view aktiv: aktueller Bereich",
                ),
                "doc switch",
            )
        if sub == "preflight":
            report = _doc_preflight_report()
            hints = _doc_preflight_hints(report)
            msg = (
                "doc preflight | "
                f"mmdc={'ok' if report.get('mmdc_path') else 'missing'} "
                f"node={'ok' if report.get('node_path') else 'missing'} "
                f"chafa={'ok' if report.get('chafa_path') else 'missing'} "
                f"playwright={'ok' if report.get('playwright_installed') else 'missing'}"
            )
            payload = {"status": "ok", "report": report, "hints": hints}
            return CommandResult(
                state.with_updates(status_message=msg),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub != "open":
            msg = "doc: open <path-to-md> | switch | mode <simple|rendered|mermaid> | preflight"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        if len(args) < 2:
            msg = "doc open: path fehlt"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        path_raw = str(args[1]).strip()
        if not path_raw:
            msg = "doc open: path fehlt"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        try:
            path = Path(path_raw).expanduser().resolve()
        except Exception as exc:
            msg = f"doc open: ungültiger Pfad ({exc})"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        if not path.exists() or not path.is_file():
            msg = f"doc open: Datei nicht gefunden ({path})"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"doc open: Datei nicht lesbar ({exc})"
            return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
        game = dict(state.header_logo_game or {})
        viewport_cfg = dict(game.get("visual_viewport") or {})
        viewport_cfg["enabled"] = True
        game["visual_viewport"] = viewport_cfg
        game["center_browser_active"] = False
        game["center_browser_status"] = "exited"
        game["visual_viewport_enabled"] = True
        game["visual_viewport_active_view_request"] = "markdown_mermaid_document"
        game["markdown_text"] = text
        _apply_doc_mode(game, "simple")
        game["center_window_view_mode"] = "simple"
        game["document_source"] = {"kind": "file", "content_or_ref": str(path), "title": path.name}
        game["_cmd_feedback"] = "doc_view: markdown_mermaid_document"
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message=f"doc_view aktiv: {path.name}",
            ),
            "doc open",
        )
    if command in {"snake-access", "snake_access"}:
        if len(args) < 2:
            return CommandResult(state, "snake-access requires: <snake-id> <cancel|view|full>", handled=False)
        snake_id = str(args[0]).strip()
        level = str(args[1]).strip().lower()
        if not snake_id:
            return CommandResult(state, "snake-access requires a snake id", handled=False)
        if level not in {"cancel", "view", "full"}:
            return CommandResult(state, "snake-access level must be cancel, view, or full", handled=False)
        game = dict(state.header_logo_game or {})
        local_id = str(game.get("local_snake_id") or "s1")
        if snake_id == local_id and level != "full":
            return CommandResult(state, "local snake must remain full", handled=False)
        remote_access_raw = game.get("remote_access")
        remote_access = dict(remote_access_raw) if isinstance(remote_access_raw, dict) else {}
        remote_access[snake_id] = level
        game["remote_access"] = remote_access

        snakes_raw = game.get("snakes")
        if isinstance(snakes_raw, dict):
            snakes = {str(k): dict(v) for k, v in snakes_raw.items() if isinstance(v, dict)}
            snap = dict(snakes.get(snake_id, {"id": snake_id}))
            snap["access_level"] = level
            snakes[snake_id] = snap
            game["snakes"] = snakes

        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"snake-access {snake_id}={level}"),
            f"snake-access {snake_id}={level}",
        )
    return CommandResult(state, f"unknown command: {command}", handled=False)
