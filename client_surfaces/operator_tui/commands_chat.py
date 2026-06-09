from __future__ import annotations

from client_surfaces.operator_tui.ai_snake_config_view import chat_model_option_label, refresh_chat_backend_models
from client_surfaces.operator_tui.ai_snake_context import get_ai_context, set_ai_context, release_notes_context
from client_surfaces.operator_tui.models import CommandResult, OperatorMode, OperatorState
from client_surfaces.operator_tui.snake_persistence import save_tui_chat_settings


def handle_chat_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else ""
    if not sub:
        return CommandResult(
            state,
            "chat: room | ai | @<snake-id> | retry | backend list|use <id>|status | model list|use <id>",
            handled=False,
        )
    game = dict(state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel, add_direct_channel
    chat = get_chat_state(game)

    if sub == "backend":
        action = args[1].lower() if len(args) > 1 else "status"
        available = game.get("chat_backends_available")
        if not isinstance(available, list) or not available:
            available = ["ananta-worker", "opencode", "lmstudio", "hermes"]
        available_norm = [str(item).strip() for item in available if str(item).strip()]
        current = str(game.get("chat_backend") or "ananta-worker").strip()
        if action == "list":
            listed = ", ".join(available_norm)
            return CommandResult(
                state.with_updates(status_message=f"chat backends: {listed}"),
                "chat backends listed",
            )
        if action == "status":
            model = str(game.get("chat_backend_model") or "-").strip() or "-"
            return CommandResult(
                state.with_updates(status_message=f"chat backend: {current} | model: {model}"),
                "chat backend status",
            )
        if action == "use":
            target = str(args[2]).strip().lower() if len(args) > 2 else ""
            if not target:
                return CommandResult(state, "chat backend use: backend-id erforderlich", handled=False)
            normalized = {item.lower(): item for item in available_norm}
            if target not in normalized:
                return CommandResult(state, f"chat backend '{target}' nicht in Liste", handled=False)
            chosen = normalized[target]
            game["chat_backend"] = chosen
            game["chat_backend_models_last_refresh_at"] = 0.0
            models, _ = refresh_chat_backend_models(game, force=True)
            current_model = str(game.get("chat_backend_model") or "").strip()
            if models and (not current_model or current_model == "-"):
                game["chat_backend_model"] = models[0]
            save_tui_chat_settings(
                {
                    "chat_backend": str(game.get("chat_backend") or ""),
                    "chat_backend_model": str(game.get("chat_backend_model") or ""),
                    "chat_backend_api_base": str(game.get("chat_backend_api_base") or ""),
                }
            )
            message = f"chat backend aktiv: {chosen}"
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=message),
                f"chat backend {chosen}",
            )
        return CommandResult(state, "chat backend: list | use <id> | status", handled=False)

    if sub == "model":
        action = args[1].lower() if len(args) > 1 else "list"
        models_raw = game.get("chat_backend_models")
        if isinstance(models_raw, list):
            models = [str(item).strip() for item in models_raw if str(item).strip()]
        else:
            models = []
        if action == "list":
            models, _ = refresh_chat_backend_models(game, force=True)
        current_model = str(game.get("chat_backend_model") or "").strip()
        if current_model and current_model not in models:
            models.insert(0, current_model)
        if action == "list":
            if not models:
                msg = "chat models: keine geladen (nutze :chat model use <id> oder setze ANANTA_TUI_CHAT_MODEL)"
            else:
                msg = "chat models: " + ", ".join(chat_model_option_label(game, model) for model in models)
            return CommandResult(state.with_updates(header_logo_game=game, status_message=msg), "chat models listed")
        if action == "use":
            target_model = " ".join(args[2:]).strip() if len(args) > 2 else ""
            if not target_model:
                return CommandResult(state, "chat model use: model-id erforderlich", handled=False)
            game["chat_backend_model"] = target_model
            if target_model not in models:
                models.append(target_model)
                game["chat_backend_models"] = models[-20:]
            save_tui_chat_settings(
                {
                    "chat_backend": str(game.get("chat_backend") or ""),
                    "chat_backend_model": str(game.get("chat_backend_model") or ""),
                    "chat_backend_api_base": str(game.get("chat_backend_api_base") or ""),
                }
            )
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    status_message=f"chat model aktiv: {target_model}",
                ),
                f"chat model {target_model}",
            )
        return CommandResult(state, "chat model: list | use <id>", handled=False)

    if sub == "retry":
        # retry failed outbox messages
        game["chat_retry_requested"] = True
        set_chat_state(game, chat)
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="chat: retry fehlgeschlagene Nachrichten"),
            "chat retry",
        )
    if sub == "room":
        switch_channel(chat, "room:main")
        set_chat_state(game, chat)
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="chat: #room"),
            "chat room",
        )
    if sub == "ai":
        switch_channel(chat, "ai:tutor")
        set_chat_state(game, chat)
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="chat: AI tutor-ai"),
            "chat ai",
        )
    if sub.startswith("@"):
        snake_id = sub[1:].strip()
        if not snake_id:
            return CommandResult(state, "chat @: snake-id erforderlich", handled=False)
        snakes_raw = game.get("snakes") or {}
        snap = snakes_raw.get(snake_id) if isinstance(snakes_raw, dict) else None
        if snap is None:
            return CommandResult(state.with_updates(status_message=f"chat: Snake '{snake_id}' nicht gefunden"), f"chat: unknown snake {snake_id}", handled=False)
        display = str(snap.get("pseudonym") or snake_id) if isinstance(snap, dict) else snake_id
        ch_id = add_direct_channel(chat, snake_id, display)
        switch_channel(chat, ch_id)
        set_chat_state(game, chat)
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"chat: @{display}"),
            f"chat direct {snake_id}",
        )
    return CommandResult(state, f"chat: unbekannte Option '{sub}'", handled=False)



def handle_notes_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else ""
    game = dict(state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel
    chat = get_chat_state(game)

    if not sub or sub == "open":
        switch_channel(chat, "notes:self")
        set_chat_state(game, chat)
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="notes: NOTES local-only"),
            "notes open",
        )
    if sub == "find":
        query = " ".join(args[1:]).strip()
        game["notes_search_query"] = query
        switch_channel(chat, "notes:self")
        set_chat_state(game, chat)
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"notes: suche '{query}'"),
            f"notes find {query}",
        )
    if sub == "pin" and len(args) > 1:
        note_id = args[1].strip()
        game["notes_pin_id"] = note_id
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"notes: pin {note_id[:12]}"),
            f"notes pin {note_id}",
        )
    if sub == "unpin" and len(args) > 1:
        note_id = args[1].strip()
        game["notes_unpin_id"] = note_id
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"notes: unpin {note_id[:12]}"),
            f"notes unpin {note_id}",
        )
    if sub == "delete" and len(args) > 1:
        note_id = args[1].strip()
        game["notes_delete_id"] = note_id
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"notes: delete {note_id[:12]}"),
            f"notes delete {note_id}",
        )
    return CommandResult(state, "notes: open | find <text> | pin <id> | unpin <id> | delete <id>", handled=False)



def handle_channels_command(args: list[str], state: OperatorState) -> CommandResult:
    game = state.header_logo_game or {}
    from client_surfaces.operator_tui.chat_state import get_chat_state
    chat = get_chat_state(game)
    channels = chat.get("channels") or {}
    parts = []
    for ch_id, ch in sorted(channels.items()):
        unread = int(ch.get("unread") or 0)
        display = str(ch.get("display_name") or ch_id)
        marker = "*" if unread else " "
        parts.append(f"{marker}{display}({'!' + str(unread) if unread else 'ok'})")
    msg = "channels: " + "  ".join(parts) if parts else "channels: keine"
    return CommandResult(state.with_updates(status_message=msg), "channels listed")



def handle_ai_context_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[1].lower() if len(args) > 1 else ""
    opt = args[2].lower() if len(args) > 2 else ""
    game = dict(state.header_logo_game or {})
    from client_surfaces.operator_tui.ai_snake_context import get_ai_context, set_ai_context, release_notes_context
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, make_message, append_message
    ctx = get_ai_context(game)
    chat = get_chat_state(game)

    if sub == "notes":
        released = opt == "on"
        release_notes_context(ctx, released=released)
        set_ai_context(game, ctx)
        # update chat state notes_context_released flag
        chat["notes_context_released"] = released
        # log to AI channel
        sys_text = f"* [system] Notes-Kontext {'freigegeben' if released else 'gesperrt'}"
        sys_msg = make_message(
            channel_id="ai:tutor", channel_type="ai",
            sender_id="system", sender_kind="system",
            text=sys_text, visibility="ai_context",
            delivery_state="received",
        )
        append_message(chat, sys_msg)
        set_chat_state(game, chat)
        label = "on" if released else "off"
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"ai context notes {label}"),
            f"ai context notes {label}",
        )
    return CommandResult(state, "ai context notes on|off", handled=False)
