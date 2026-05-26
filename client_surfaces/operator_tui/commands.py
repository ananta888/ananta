from __future__ import annotations

from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.sections import move_section, normalize_section_id, section_ids


def execute_command(raw_command: str, state: OperatorState) -> CommandResult:
    text = str(raw_command or "").strip()
    if text.startswith(":"):
        text = text[1:].strip()
    if not text:
        return CommandResult(state.with_updates(mode=OperatorMode.NORMAL, command_line=""), "empty command ignored")

    parts = text.split()
    command = parts[0].lower()
    args = parts[1:]

    if command in {"refresh", "r"}:
        return CommandResult(
            state.with_updates(
                mode=OperatorMode.NORMAL,
                command_line="",
                refresh_count=state.refresh_count + 1,
                status_message="refresh requested",
            ),
            "refresh requested",
        )
    if command in {"section", "open", "goto"}:
        if not args:
            return CommandResult(state.with_updates(mode=OperatorMode.COMMAND), "section command requires a section id")
        section_id = normalize_section_id(args[0])
        return CommandResult(
            state.with_updates(
                mode=OperatorMode.NORMAL,
                command_line="",
                section_id=section_id,
                selected_index=0,
                status_message=f"section {section_id}",
            ),
            f"opened section {section_id}",
        )
    if command == "next":
        section_id = move_section(state.section_id, 1)
        return CommandResult(state.with_updates(section_id=section_id, selected_index=0), f"opened section {section_id}")
    if command == "prev":
        section_id = move_section(state.section_id, -1)
        return CommandResult(state.with_updates(section_id=section_id, selected_index=0), f"opened section {section_id}")
    if command == "focus":
        if not args:
            return CommandResult(state, "focus command requires navigation, content, or detail")
        requested = args[0].lower()
        try:
            focus = FocusPane(requested)
        except ValueError:
            return CommandResult(state, f"unknown focus pane: {requested}", handled=False)
        return CommandResult(state.with_updates(focus=focus, status_message=f"focus {focus.value}"), f"focus {focus.value}")
    if command == "mode":
        if not args:
            return CommandResult(state, "mode command requires normal, command, inspect, or edit")
        requested = args[0].lower()
        try:
            mode = OperatorMode(requested)
        except ValueError:
            return CommandResult(state, f"unknown mode: {requested}", handled=False)
        return CommandResult(state.with_updates(mode=mode, status_message=f"mode {mode.value}"), f"mode {mode.value}")
    if command in {"help", "?"}:
        return CommandResult(state.with_updates(show_help=not state.show_help, status_message="help toggled"), "help toggled")
    if command == "mouse":
        mode = (args[0].strip().lower() if args else "toggle")
        if mode not in {"on", "off", "toggle"}:
            return CommandResult(state, "mouse command requires on, off, or toggle", handled=False)
        game = dict(state.header_logo_game or {})
        current = bool(game.get("mouse_follow_enabled"))
        if mode == "toggle":
            next_value = not current
        else:
            next_value = mode == "on"
        game["mouse_follow_enabled"] = next_value
        game["movement_mode"] = "mouse_follow" if next_value else "keyboard"
        label = "on" if next_value else "off"
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"mouse-follow {label}"),
            f"mouse-follow {label}",
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
    if command == "inspect":
        return CommandResult(state.with_updates(mode=OperatorMode.INSPECT, status_message="inspect current selection"), "inspect current selection")
    if command == "browser":
        target = args[0] if args else ""
        url = browser_fallback_url(state.endpoint, state.section_id, target)
        return CommandResult(state.with_updates(browser_fallback_url=url, status_message=f"browser fallback {url}"), f"browser fallback {url}")
    if command == "action":
        if not args:
            return CommandResult(state, "action command requires an action name", handled=False)
        risk = args[1] if len(args) > 1 else "read_only"
        action = parse_action(args[0], risk=risk)
        result = dispatch_action(action)
        pending = (
            {
                "name": result.pending_action.name,
                "target": result.pending_action.target,
                "risk": result.pending_action.risk.value,
                "payload": dict(result.pending_action.payload),
                "requires_confirmation": result.pending_action.requires_confirmation,
            }
            if result.pending_action
            else None
        )
        return CommandResult(
            state.with_updates(
                pending_action=pending,
                audit_context=result.audit_context,
                status_message=result.message,
            ),
            result.message,
            handled=result.accepted or result.pending_action is not None,
        )
    if command == "confirm":
        pending = state.pending_action or {}
        if not pending:
            return CommandResult(state, "no pending action to confirm", handled=False)
        action = parse_action(str(pending.get("name") or ""), str(pending.get("target") or ""), str(pending.get("risk") or "high"))
        result = dispatch_action(action, confirmed=True)
        return CommandResult(
            state.with_updates(pending_action=None, audit_context=result.audit_context, status_message=result.message),
            result.message,
            handled=result.accepted,
        )
    if command in {"cancel", "esc"}:
        return CommandResult(
            state.with_updates(mode=OperatorMode.NORMAL, pending_action=None, command_line="", status_message="cancelled"),
            "cancelled",
        )
    if command == "sections":
        return CommandResult(state.with_updates(status_message="sections: " + ",".join(section_ids())), "sections listed")

    # ── speed ─────────────────────────────────────────────────────────────────
    if command == "speed":
        if not args:
            return CommandResult(state, "speed requires a level 1-5", handled=False)
        try:
            level = int(args[0])
        except ValueError:
            return CommandResult(state.with_updates(status_message="speed: ungültiger Wert (1-5)"), "speed: invalid", handled=False)
        if level < 1 or level > 5:
            return CommandResult(state.with_updates(status_message="speed: Wert muss 1-5 sein"), "speed: out of range", handled=False)
        # Map level 1-5 to TPS: 3, 6, 12, 24, 60
        tps_map = {1: 3, 2: 6, 3: 12, 4: 24, 5: 60}
        tps = tps_map[level]
        game = dict(state.header_logo_game or {})
        game["tps_override"] = tps
        game["speed_level"] = level
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"speed: {level}/5 ({tps} tps)"),
            f"speed {level}/5",
        )

    # ── tutor ─────────────────────────────────────────────────────────────────
    if command == "tutor":
        sub = args[0].lower() if args else ""
        if sub == "mode":
            mode_arg = args[1].lower() if len(args) > 1 else ""
            if mode_arg not in {"overview", "deep", "expert"}:
                return CommandResult(state, "tutor mode erwartet: overview | deep | expert", handled=False)
            try:
                from client_surfaces.operator_tui.snake_persistence import set_tutor_mode
                set_tutor_mode(mode_arg)
            except Exception:
                pass
            game = dict(state.header_logo_game or {})
            game["tutor_depth_mode"] = mode_arg
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=f"tutor mode: {mode_arg}"),
                f"tutor mode {mode_arg}",
            )
        if sub == "silent":
            try:
                from client_surfaces.operator_tui.snake_persistence import set_tutor_silent
                set_tutor_silent(True)
            except Exception:
                pass
            game = dict(state.header_logo_game or {})
            game["tutor_silent"] = True
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="tutor: idle-Kommentare deaktiviert"),
                "tutor silent",
            )
        if sub == "active":
            try:
                from client_surfaces.operator_tui.snake_persistence import set_tutor_silent
                set_tutor_silent(False)
            except Exception:
                pass
            game = dict(state.header_logo_game or {})
            game["tutor_silent"] = False
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="tutor: idle-Kommentare aktiv"),
                "tutor active",
            )
        if sub == "replay":
            section_arg = args[1].lower() if len(args) > 1 else ""
            try:
                from client_surfaces.operator_tui.snake_persistence import load_tutor_config, save_tutor_config
                cfg = load_tutor_config()
                visited = list(cfg.get("visited_sections") or [])
                if section_arg in visited:
                    visited.remove(section_arg)
                    cfg["visited_sections"] = visited
                    save_tutor_config(cfg)
            except Exception:
                pass
            return CommandResult(
                state.with_updates(status_message=f"tutor replay: {section_arg or '(alle)'} zurückgesetzt"),
                f"tutor replay {section_arg}",
            )
        return CommandResult(state, "tutor: mode <overview|deep|expert> | silent | active | replay <section>", handled=False)

    # ── ask ───────────────────────────────────────────────────────────────────
    if command == "ask":
        question = " ".join(args).strip()
        if not question:
            return CommandResult(state.with_updates(status_message="ask: Bitte Frage angeben"), "ask: leer", handled=False)
        game = dict(state.header_logo_game or {})
        game["tutor_ask_question"] = question
        game["tutor_ask_at"] = __import__("time").monotonic()
        game["tutor_ask_answered"] = False
        # pause game while AI answers
        game["paused"] = True
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message=f"ask: {question[:40]}...",
            ),
            f"ask: {question[:40]}",
        )

    # ── tutorial ──────────────────────────────────────────────────────────────
    if command == "tutorial":
        sub = args[0].lower() if args else ""
        if sub == "start":
            name = args[1] if len(args) > 1 else "intro"
            try:
                from client_surfaces.operator_tui.snake_tutorial import make_tutorial_state
                from client_surfaces.operator_tui.snake_persistence import get_tutorial_progress
                start_step = max(0, get_tutorial_progress(name))
                ts = make_tutorial_state(name, start_step=start_step)
            except Exception:
                ts = None
            if ts is None:
                return CommandResult(state.with_updates(status_message=f"tutorial: '{name}' nicht gefunden"), f"tutorial not found: {name}", handled=False)
            game = dict(state.header_logo_game or {})
            game["tutorial_state"] = ts
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"tutorial: {ts['title']} gestartet"),
                f"tutorial start {name}",
            )
        if sub == "stop":
            game = dict(state.header_logo_game or {})
            game["tutorial_state"] = None
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="tutorial: gestoppt"),
                "tutorial stop",
            )
        if sub == "skip":
            game = dict(state.header_logo_game or {})
            ts = dict(game.get("tutorial_state") or {})
            if not ts:
                return CommandResult(state.with_updates(status_message="tutorial: kein aktives Tutorial"), "tutorial: none active", handled=False)
            try:
                from client_surfaces.operator_tui.snake_tutorial import advance_step, get_current_step
                step = get_current_step(ts)
                ts = advance_step(ts, skipped=True)
                game["tutorial_state"] = ts
            except Exception:
                pass
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="tutorial: Step übersprungen"),
                "tutorial skip",
            )
        if sub == "reset":
            game = dict(state.header_logo_game or {})
            ts_raw = game.get("tutorial_state")
            name = str((ts_raw or {}).get("name") or "intro") if isinstance(ts_raw, dict) else "intro"
            try:
                from client_surfaces.operator_tui.snake_tutorial import make_tutorial_state
                from client_surfaces.operator_tui.snake_persistence import reset_tutorial_progress
                reset_tutorial_progress(name)
                ts = make_tutorial_state(name, start_step=0)
                game["tutorial_state"] = ts
            except Exception:
                game["tutorial_state"] = None
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"tutorial: {name} zurückgesetzt"),
                f"tutorial reset {name}",
            )
        if sub == "guided":
            game = dict(state.header_logo_game or {})
            ts_raw = game.get("tutorial_state")
            if isinstance(ts_raw, dict) and ts_raw.get("active"):
                ts = dict(ts_raw)
                ts["guided"] = True
                game["tutorial_state"] = ts
                return CommandResult(
                    state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="tutorial: Guided Mode aktiviert"),
                    "tutorial guided",
                )
            return CommandResult(state.with_updates(status_message="tutorial: erst :tutorial start <name>"), "tutorial: none active", handled=False)
        return CommandResult(state, "tutorial: start <name> | stop | skip | reset | guided", handled=False)

    # ── tutorials ─────────────────────────────────────────────────────────────
    if command == "tutorials":
        try:
            from client_surfaces.operator_tui.snake_tutorial import list_tutorials
            items = list_tutorials()
            names = ", ".join(f"{t['name']} ({t['step_count']} Steps)" for t in items) if items else "(keine)"
        except Exception:
            names = "(Ladefehler)"
        return CommandResult(state.with_updates(status_message=f"tutorials: {names}"), "tutorials listed")

    # ── snakes ────────────────────────────────────────────────────────────────
    if command == "snakes":
        game = state.header_logo_game or {}
        snakes_raw = game.get("snakes")
        snakes = {str(k): dict(v) for k, v in snakes_raw.items() if isinstance(v, dict)} if isinstance(snakes_raw, dict) else {}
        if not snakes:
            return CommandResult(state.with_updates(status_message="snakes: keine aktiven Schlangen"), "snakes: empty")
        parts = []
        for sid, snap in sorted(snakes.items()):
            pseudo = str(snap.get("pseudonym") or sid)
            color = str(snap.get("snake_color") or "mint")
            role = str(snap.get("role") or ("player" if snap.get("local") else "tutor"))
            parts.append(f"{sid}={pseudo}[{color}/{role}]")
        return CommandResult(state.with_updates(status_message="snakes: " + " ".join(parts)), "snakes listed")

    # ── msg ───────────────────────────────────────────────────────────────────
    if command == "msg":
        if len(args) < 2:
            return CommandResult(state, "msg erwartet: <snake-id> <text>", handled=False)
        target_id = args[0].strip()
        text = " ".join(args[1:]).strip()
        if not text:
            return CommandResult(state.with_updates(status_message="msg: leere Nachricht ignoriert"), "msg: empty", handled=False)
        if len(text) > 200:
            return CommandResult(state.with_updates(status_message="msg: max. 200 Zeichen"), "msg: too long", handled=False)
        game = dict(state.header_logo_game or {})
        outbox: list[dict] = list(game.get("snake_outbox") or [])
        outbox.append({
            "to": target_id,
            "from": str(game.get("local_snake_id") or "s1"),
            "text": text,
            "at": __import__("time").monotonic(),
        })
        game["snake_outbox"] = outbox[-20:]  # keep last 20
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"msg → {target_id}: {text[:40]}"),
            f"msg sent to {target_id}",
        )

    return CommandResult(state.with_updates(status_message=f"unknown command: {command}"), f"unknown command: {command}", handled=False)
