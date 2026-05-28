# AI-Snake Config — User JSON Persistence

Every AI-Snake visual and chat config change is saved immediately and flushed to disk
on exit (Ctrl-Q).

## File paths

| File | Purpose | When written |
|---|---|---|
| `<CWD>/user.json` | Project-specific settings | On every config change + Ctrl-Q |
| `~/.anana/user.json` | User-global defaults | On Ctrl-Q flush only |

## Load order (merge)

```
_DEFAULTS → ~/.anana/user.json → <CWD>/user.json
```

Project file wins over global. Global wins over built-in defaults.

## Immediate write on change

Every call to `apply_ai_snake_config_value()` calls `_persist_tui_chat_settings()` which
calls `UserConfigManager.save()`. This writes `<CWD>/user.json` atomically using a
temp-file + `os.replace()` pattern.

```
settings change
  → _persist_tui_chat_settings(game)
  → UserConfigManager.save(payload)
  → write .user.json.tmp
  → os.replace(.tmp → user.json)   ← atomic on POSIX
```

If the write fails, the error is logged and the TUI continues without crashing.
The legacy `~/.config/ananta/tui_chat_settings.json` is still written for backward
compatibility with old code paths that read from `load_tui_chat_settings()`.

## Flush on Ctrl-Q

`_handle_quit_key` in `interactive.py` calls `flush_user_config(game)` before
`event.app.exit()`. This writes both:
- `<CWD>/user.json` — updated project settings
- `~/.anana/user.json` — updated global user defaults

The flush completes in the current thread. If it takes too long or fails, the TUI
exits anyway.

## JSON schema

```json
{
  "schema_version": "user_config.v1",
  "updated": "2025-05-28T12:00:00Z",
  "settings": {
    "tutorial_mode": false,
    "ai_snake_provider_preference": "lmstudio",
    "ai_visual_use_codecompass": false,
    "chat_panel_open": true,
    "chat_backend": "ananta-worker",
    "chat_backend_model": "",
    "chat_backend_api_base": "http://localhost:1234/v1",
    "chat_ask_timeout_s": 45.0,
    "chat_use_codecompass": true,
    "chat_include_local_project": true,
    "chat_include_wikipedia": false,
    "chat_source_pack_id": "ananta-dev-default",
    "chat_context_chars": 3000,
    "chat_max_tokens": 400,
    "chat_rag_top_k": 24,
    "chat_answer_chars": 6000,
    "chat_use_history": true,
    "chat_history_turns": 6,
    "chat_history_chars": 1800,
    "chat_use_summary": true,
    "chat_summary_chars": 1500,
    "chat_summary_update_every_turns": 3,
    "chat_pass_memory_to_worker": true,
    "chat_worker_mode": "snake_ask",
    "chat_backend_fallback": "lmstudio",
    "chat_include_runtime_status": false
  }
}
```

Only keys in `_SCHEMA_KEYS` are written. Unknown keys from game state are stripped before
write. Non-primitive values (lists, dicts) are also stripped.

## UserConfigManager API

```python
from client_surfaces.operator_tui.config.user_config_manager import (
    UserConfigManager, load_user_config, save_user_config, flush_user_config,
)

# Load merged settings
settings = load_user_config()                   # uses CWD
settings = load_user_config(cwd=Path("/proj"))  # project-specific

# Save single change
save_user_config({"chat_backend": "lmstudio"})

# Flush all config from game state (called on Ctrl-Q)
project_ok, global_ok = flush_user_config(game)

# Apply persisted config to game dict (called on startup)
mgr = UserConfigManager(cwd=Path.cwd())
game = mgr.apply_to_game(game)

# Diagnostics
print(mgr.diagnostics())
# {global_path, project_path, global_exists, project_exists, cache_keys, schema_version}
```

## Error handling

| Failure | Behaviour |
|---|---|
| Directory not writable | `save()` returns `False`, logs warning, TUI continues |
| JSON parse error on load | Returns empty dict, falls back to defaults |
| Temp file left behind | `os.replace()` failure removes `.tmp` silently |
| Flush fails on exit | TUI exits anyway, warning logged |

## Backward compatibility

`snake_persistence.save_tui_chat_settings()` continues to be called after every
`_persist_tui_chat_settings()`, keeping `~/.config/ananta/tui_chat_settings.json`
updated. Old code paths reading from `load_tui_chat_settings()` remain functional.
