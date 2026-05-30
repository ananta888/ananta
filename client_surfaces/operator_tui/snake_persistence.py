"""Persistence helpers for snake highscore and tutor configuration.

Config directory: ~/.config/ananta/
  snake_scores.json   – highscore, last score, game count
  tutor_config.json   – tutor depth mode, visited sections, tutorial progress
  oidc_token.json     – cached OIDC access token (chmod 600)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    return Path.home() / ".config" / "ananta"


# ── highscore ─────────────────────────────────────────────────────────────────


def load_snake_scores() -> dict[str, Any]:
    path = _config_dir() / "snake_scores.json"
    if not path.exists():
        return {"high": 0, "last": 0, "games": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"high": 0, "last": 0, "games": 0}
    except Exception:
        return {"high": 0, "last": 0, "games": 0}


def save_snake_score(score: int) -> dict[str, Any]:
    """Persist a finished game score. Returns updated scores dict."""
    prev = load_snake_scores()
    updated: dict[str, Any] = {
        "high": max(int(prev.get("high") or 0), score),
        "last": score,
        "games": int(prev.get("games") or 0) + 1,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = _config_dir() / "snake_scores.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    except Exception:
        pass
    return updated


# ── tutor config ──────────────────────────────────────────────────────────────

_DEFAULT_TUTOR_CFG: dict[str, Any] = {
    "mode": "overview",
    "silent": False,
    "visited_sections": [],
    "tutorial_progress": {},
}


def load_tutor_config() -> dict[str, Any]:
    path = _config_dir() / "tutor_config.json"
    if not path.exists():
        return dict(_DEFAULT_TUTOR_CFG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = dict(_DEFAULT_TUTOR_CFG)
        if isinstance(data, dict):
            cfg.update(data)
        return cfg
    except Exception:
        return dict(_DEFAULT_TUTOR_CFG)


def save_tutor_config(cfg: dict[str, Any]) -> None:
    path = _config_dir() / "tutor_config.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def get_tutor_mode() -> str:
    """Return current tutor depth mode: overview | deep | expert."""
    return str(load_tutor_config().get("mode") or "overview")


def set_tutor_mode(mode: str) -> None:
    if mode not in {"overview", "deep", "expert"}:
        return
    cfg = load_tutor_config()
    cfg["mode"] = mode
    save_tutor_config(cfg)


def is_tutor_silent() -> bool:
    return bool(load_tutor_config().get("silent"))


def set_tutor_silent(silent: bool) -> None:
    cfg = load_tutor_config()
    cfg["silent"] = silent
    save_tutor_config(cfg)


def mark_section_visited(section_id: str) -> bool:
    """Mark a section as visited. Returns True if this was the first visit."""
    cfg = load_tutor_config()
    visited: list[str] = list(cfg.get("visited_sections") or [])
    if section_id in visited:
        return False
    visited.append(section_id)
    cfg["visited_sections"] = visited
    save_tutor_config(cfg)
    return True


def get_visited_sections() -> list[str]:
    return list(load_tutor_config().get("visited_sections") or [])


# ── tutorial progress ─────────────────────────────────────────────────────────


def save_tutorial_progress(tutorial_name: str, step_idx: int) -> None:
    cfg = load_tutor_config()
    progress: dict[str, Any] = dict(cfg.get("tutorial_progress") or {})
    progress[tutorial_name] = {"step": step_idx, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    cfg["tutorial_progress"] = progress
    save_tutor_config(cfg)


def get_tutorial_progress(tutorial_name: str) -> int:
    """Return last completed step index (0-based), or -1 if not started."""
    cfg = load_tutor_config()
    progress = cfg.get("tutorial_progress") or {}
    entry = progress.get(tutorial_name)
    if isinstance(entry, dict):
        return int(entry.get("step") or 0)
    return -1


def reset_tutorial_progress(tutorial_name: str) -> None:
    cfg = load_tutor_config()
    progress = dict(cfg.get("tutorial_progress") or {})
    progress.pop(tutorial_name, None)
    cfg["tutorial_progress"] = progress
    save_tutor_config(cfg)


# ── OIDC token cache ─────────────────────────────────────────────────────────

_OIDC_TOKEN_FILE = "oidc_token.json"
_OIDC_TOKEN_MARGIN = 60  # Sekunden Sicherheitspuffer vor Ablauf


def _oidc_token_path() -> Path:
    return _config_dir() / _OIDC_TOKEN_FILE


def _decode_jwt_exp(token: str) -> float:
    """Liest exp-Claim aus JWT-Payload ohne Krypto-Dependency. 0.0 bei Fehler."""
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return 0.0
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad))
        return float(claims.get("exp") or 0)
    except Exception:
        return 0.0


def _decode_jwt_username(token: str) -> str:
    """Liest preferred_username/email/sub aus JWT-Payload."""
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return ""
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad))
        return str(
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("sub")
            or ""
        )
    except Exception:
        return ""


def save_oidc_token(token: str, *, issuer: str = "", username: str = "") -> None:
    """Persistiert OIDC-Token in ~/.config/ananta/oidc_token.json (chmod 600)."""
    if not token:
        return
    exp = _decode_jwt_exp(token)
    resolved_username = username or _decode_jwt_username(token)
    entry: dict[str, Any] = {
        "access_token": token,
        "exp": exp,
        "issuer": issuer,
        "username": resolved_username,
        "saved_at": time.time(),
    }
    path = _oidc_token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        path.chmod(0o600)
    except Exception:
        pass


def load_oidc_token() -> dict[str, Any] | None:
    """Lädt gecachten OIDC-Token. Gibt None zurück wenn nicht vorhanden oder abgelaufen."""
    path = _oidc_token_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        token = str(data.get("access_token") or "")
        if not token:
            return None
        exp = float(data.get("exp") or 0)
        if exp > 0 and time.time() >= exp - _OIDC_TOKEN_MARGIN:
            return None  # abgelaufen
        return data
    except Exception:
        return None


def clear_oidc_token() -> None:
    """Löscht den gecachten OIDC-Token."""
    try:
        _oidc_token_path().unlink(missing_ok=True)
    except Exception:
        pass


# ── TUI chat/config settings (per cwd) ───────────────────────────────────────

_TUI_SETTINGS_FILE = "tui_chat_settings.json"


def _tui_settings_path() -> Path:
    return _config_dir() / _TUI_SETTINGS_FILE


def _cwd_key(cwd: str | Path | None = None) -> str:
    path = Path(cwd) if cwd is not None else Path.cwd()
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path)


def _load_tui_settings_store() -> dict[str, Any]:
    path = _tui_settings_path()
    if not path.exists():
        return {"scopes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"scopes": {}}
    if not isinstance(data, dict):
        return {"scopes": {}}
    scopes = data.get("scopes")
    if not isinstance(scopes, dict):
        data["scopes"] = {}
    return data


def _save_tui_settings_store(store: dict[str, Any]) -> None:
    path = _tui_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def load_tui_chat_settings(*, cwd: str | Path | None = None) -> dict[str, Any]:
    store = _load_tui_settings_store()
    scopes = store.get("scopes")
    if not isinstance(scopes, dict):
        return {}
    data = scopes.get(_cwd_key(cwd))
    return dict(data) if isinstance(data, dict) else {}


def save_tui_chat_settings(settings: dict[str, Any], *, cwd: str | Path | None = None) -> None:
    if not isinstance(settings, dict):
        return
    store = _load_tui_settings_store()
    scopes = store.get("scopes")
    if not isinstance(scopes, dict):
        scopes = {}
        store["scopes"] = scopes
    clean: dict[str, Any] = {}
    for key, value in settings.items():
        if isinstance(value, (str, int, float, bool)) and str(key).strip():
            clean[str(key)] = value
    scopes[_cwd_key(cwd)] = clean
    store["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_tui_settings_store(store)
