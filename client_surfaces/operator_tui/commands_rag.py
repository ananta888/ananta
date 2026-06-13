from __future__ import annotations

import json
import urllib.request

from client_surfaces.operator_tui.models import CommandResult, OperatorMode, OperatorState


def handle_rag_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else ""
    if sub not in ("why",):
        return CommandResult(
            state.with_updates(status_message="rag: Nutzung: :rag why <frage>"),
            "rag: usage",
            handled=False,
        )
    rest = args[1:]
    as_json = rest and rest[0] == "--json"
    if as_json:
        rest = rest[1:]
    question = " ".join(rest).strip()
    if not question:
        return CommandResult(
            state.with_updates(status_message="rag why: Bitte Frage angeben — z.B. :rag why warum sehe ich keine Tests?"),
            "rag why: leer",
            handled=False,
        )

    from client_surfaces.operator_tui.chat_state import (
        get_chat_state, get_effective_chat_settings, active_session_id,
        make_message, append_message, ChannelType, SenderKind, DeliveryState, Visibility,
    )
    game = dict(state.header_logo_game or {})
    chat = get_chat_state(game)
    eff = get_effective_chat_settings(chat, game)

    endpoint = str(getattr(state, "endpoint", "") or "").rstrip("/") or "http://localhost:5000"
    retrieval_config: dict[str, object] = {}
    for k in (
        "chat_retrieval_profile",
        "chat_retrieval_domain_hint",
        "chat_codecompass_trigger_mode",
        "chat_code_questions_repo_first",
        "chat_use_codecompass",
        "chat_include_local_project",
        "chat_include_wikipedia",
        "chat_include_task_memory",
        "chat_source_pack_id",
    ):
        if k in eff:
            retrieval_config[k] = eff[k]

    dry: dict[str, object] = {}
    try:
        payload = json.dumps({
            "question": question,
            "trace_only": True,
            "debug": True,
            "retrieval_config": retrieval_config,
        }).encode()
        req = urllib.request.Request(
            f"{endpoint}/snake/ask",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            dry = dict(data.get("rag_why") or {})
    except Exception as exc:
        # Fallback: local profile resolution only
        try:
            from agent.services.retrieval_profile_service import resolve_profile
            from worker.retrieval.codecompass_candidate_resolver import ResolverConfig
            profile = resolve_profile(question, eff)
            scope = ResolverConfig.from_env()
            dry = {
                "retrieval_profile": profile.as_dict(),
                "resolver_scope": {
                    "include_source": scope.include_source,
                    "include_test_paths": scope.include_test_paths,
                    "include_docs": scope.include_docs,
                    "include_workflows": scope.include_workflows,
                    "include_third_party": scope.include_third_party,
                },
                "candidate_counts": {"total": "n/a (hub offline)", "by_source_type": {}},
                "top_sources": [],
                "preset_hints": [],
                "hub_error": str(exc)[:80],
            }
        except Exception as exc2:
            dry = {"error": str(exc2)[:120]}

    # ── Format output ──────────────────────────────────────────────────────
    if as_json:
        text = json.dumps(dry, indent=2, ensure_ascii=False)
    else:
        lines: list[str] = []
        sep = "─" * 54
        lines.append(f"╔ /rag why: {question[:45]}")
        lines.append(sep)

        sess_id = active_session_id(chat)
        sess_name = ""
        try:
            from client_surfaces.operator_tui.chat_state import get_active_session
            s = get_active_session(chat)
            sess_name = str((s or {}).get("name") or "") if s else ""
        except Exception:
            pass
        backend = str(eff.get("chat_backend") or game.get("chat_backend") or "?")
        lines.append(f"Session   : {sess_name or sess_id or '(default)'} | backend: {backend}")

        prof = dry.get("retrieval_profile") or {}
        if isinstance(prof, dict):
            pid = str(prof.get("profile_id") or "?")
            dom = str(prof.get("domain") or "?")
            intent = str(prof.get("intent") or "?")
            flag = str(prof.get("feature_flag") or "auto")
            tmode = str(prof.get("trigger_mode") or "auto")
            sel = str(prof.get("selected_by") or "?")
            reasons = list(prof.get("reasons") or [])[:4]
            src_types = list(prof.get("source_types") or [])
            weights = dict(prof.get("source_type_weights") or {})
            neg = list(prof.get("negative_source_patterns") or [])
            warns = list(prof.get("warnings") or [])
            lines.append(f"Profil    : {pid} [{flag}]")
            lines.append(f"Domain    : {dom} / {intent}")
            lines.append(f"Trigger   : {tmode} | selected_by: {sel}")
            if reasons:
                lines.append(f"Gründe    : {', '.join(reasons)}")
            w_str = " ".join(f"{k}({v:.1f})" for k, v in weights.items()) if weights else "–"
            lines.append(f"Sources   : {', '.join(src_types) or '–'} | Gewichte: {w_str}")
            if neg:
                lines.append(f"Negativ   : {', '.join(neg[:3])}")
            if warns:
                lines.append(f"Warnings  : {'; '.join(warns[:3])}")
        else:
            lines.append(f"Profil    : {prof}")

        scope = dry.get("resolver_scope") or {}
        if isinstance(scope, dict):
            def _flag(v: object) -> str:
                return "✓" if v else "✗"
            lines.append(
                f"Scope     : src={_flag(scope.get('include_source'))} "
                f"tests={_flag(scope.get('include_test_paths'))} "
                f"docs={_flag(scope.get('include_docs'))} "
                f"workflows={_flag(scope.get('include_workflows'))} "
                f"3rd={_flag(scope.get('include_third_party'))}"
            )

        counts = dry.get("candidate_counts") or {}
        if isinstance(counts, dict):
            total = counts.get("total", 0)
            by_src = dict(counts.get("by_source_type") or {})
            src_detail = " ".join(f"{k}:{v}" for k, v in sorted(by_src.items())) if by_src else ""
            lines.append(f"Kandidaten: {total}{(' ('+src_detail+')') if src_detail else ''}")

        top = list(dry.get("top_sources") or [])[:5]
        if top:
            lines.append("Top-Quellen:")
            for src in top:
                p = str(src.get("path") or "?")[-55:]
                st = str(src.get("source_type") or "?")
                sc = src.get("score", "")
                lines.append(f"  {p} [{st}{'|'+str(sc) if sc else ''}]")

        degraded = list(dry.get("degraded_channels") or [])
        if degraded:
            lines.append(f"Degradiert: {'; '.join(degraded[:2])}")

        hints = list(dry.get("preset_hints") or [])
        for h in hints:
            lines.append(f"→ {h}")

        if dry.get("hub_error"):
            lines.append(f"Hub-Fehler: {dry['hub_error']}")
        if dry.get("error"):
            lines.append(f"Fehler    : {dry['error']}")

        lines.append("╚" + sep[1:])
        text = "\n".join(lines)

    # Inject as system message into the active / AI channel
    msg = make_message(
        channel_id="ai:tutor",
        channel_type=ChannelType.AI,
        sender_id="system",
        sender_kind=SenderKind.SYSTEM,
        text=text,
        delivery_state=DeliveryState.RECEIVED,
        visibility=Visibility.ROOM,
    )
    append_message(chat, msg)
    game["chat_state"] = chat
    return CommandResult(
        state.with_updates(
            header_logo_game=game,
            mode=OperatorMode.NORMAL,
            command_line="",
            status_message=f"rag why: {question[:40]}",
        ),
        f"rag why: {question[:40]}",
    )



def handle_te_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else "status"

    if sub == "status":
        try:
            from agent.services.task_engine_status_service import get_task_engine_status_service
            s = get_task_engine_status_service().as_dict()
            lines = [
                f"Task Engine Status",
                f"  active      : {s.get('active')}",
                f"  intent      : {s.get('intent') or '—'}",
                f"  task_class  : {s.get('task_class') or '—'}",
                f"  llm_required: {s.get('llm_required')}",
                f"  handler     : {s.get('handler_id') or '—'}",
                f"  bypassed_llm: {s.get('bypassed_llm')}",
                f"  reason      : {s.get('reason') or '—'}",
                f"  task_id     : {s.get('task_id') or '—'}",
            ]
            msg = " | ".join(lines[:4])
        except Exception as exc:
            msg = f"te status error: {exc}"
        from client_surfaces.operator_tui.snake_chat import make_message, append_message
        append_message(make_message("ai:tutor", "\n".join(lines if 'lines' in dir() else [msg]), role="system"), state, game)
        return CommandResult(state.with_updates(status_message=msg), msg)

    if sub == "classify":
        kind = args[1] if len(args) > 1 else ""
        if not kind:
            return CommandResult(state.with_updates(status_message="te classify: Bitte task_kind angeben"), "te classify: no kind")
        try:
            from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
            gate = TaskEnginePolicyGate.from_settings()
            d = gate.evaluate({"task_kind": kind})
            msg = f"te classify '{kind}': class={d.task_class} llm={d.llm_required} handler={d.handler_id or '—'} reason={d.reason}"
        except Exception as exc:
            msg = f"te classify error: {exc}"
        return CommandResult(state.with_updates(status_message=msg[:120]), msg)

    return CommandResult(state.with_updates(status_message="te: Nutzung: :te status | :te classify <kind>"), "te: unknown sub")



def handle_sim_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else "help"
    try:
        from simulation.cli.commands import cmd_sim
        messages: list[str] = []
        result_data = cmd_sim([sub] + args[1:], output_fn=messages.append)
        msg = " | ".join(messages) if messages else "sim: ok"
    except Exception as exc:
        msg = f"sim error: {exc}"
    return CommandResult(state.with_updates(status_message=msg[:160]), msg)



def handle_tutorial_command(args: list[str], state: OperatorState) -> CommandResult:
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



def handle_tutorials_command(args: list[str], state: OperatorState) -> CommandResult:
    try:
        from client_surfaces.operator_tui.snake_tutorial import list_tutorials
        items = list_tutorials()
        names = ", ".join(f"{t['name']} ({t['step_count']} Steps)" for t in items) if items else "(keine)"
    except Exception:
        names = "(Ladefehler)"
    return CommandResult(state.with_updates(status_message=f"tutorials: {names}"), "tutorials listed")



def handle_snakes_command(args: list[str], state: OperatorState) -> CommandResult:
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



def handle_msg_command(args: list[str], state: OperatorState) -> CommandResult:
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
