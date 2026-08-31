"""Persistence adapter for legacy agent tokens and optional dotenv updates."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

_LOG = logging.getLogger("agent.config")


def update_dotenv(key: str, value: str, *, dotenv_path: str = ".env") -> None:
    if not os.path.exists(dotenv_path):
        return
    try:
        with open(dotenv_path, encoding="utf-8") as handle:
            lines = handle.readlines()
        updated = False
        new_lines: list[str] = []
        for line in lines:
            if line.startswith(f"{key}=") or line.startswith(f"export {key}="):
                new_lines.append(f"{key}={value}\n")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"{key}={value}\n")
        with open(dotenv_path, "w", encoding="utf-8") as handle:
            handle.writelines(new_lines)
        _LOG.info("%s in .env aktualisiert.", key)
    except Exception as exc:
        _LOG.error("Fehler beim Aktualisieren der .env: %s", exc)


def save_agent_token(settings: Any, token: str) -> None:
    settings.agent_token = token
    if not settings.agent_token_persistence:
        return
    try:
        os.makedirs(settings.secrets_dir, exist_ok=True)
        path = settings.token_path
        data = {"agent_token": token, "last_rotation": time.time()}
        if not os.path.exists(path):
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
                os.close(descriptor)
            except Exception:
                pass
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        if settings.auto_update_dotenv:
            update_dotenv("AGENT_TOKEN", token)
        _LOG.info("Agent Token erfolgreich in %s persistiert.", path)
    except Exception as exc:
        _LOG.error("Fehler beim Persistieren des Agent Tokens: %s", exc)
