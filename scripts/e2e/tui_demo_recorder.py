from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import re
import select
import shlex
import struct
import subprocess
import termios
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _asciinema_v2_lines(
    *,
    title: str,
    frames: list[tuple[float, str]],
    width: int = 104,
    height: int = 30,
) -> str:
    header = {
        "version": 2,
        "width": max(40, int(width)),
        "height": max(12, int(height)),
        "title": title,
        "env": {"TERM": "xterm-256color", "COLORTERM": "truecolor"},
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    for timestamp, frame in frames:
        lines.append(json.dumps([float(timestamp), "o", frame], ensure_ascii=False))
    return "\n".join(lines) + "\n"


def _default_tui_command(*, section: str | None = None, focus: str | None = None) -> str:
    base = ".venv/bin/ananta tui" if Path(".venv/bin/ananta").exists() else "ananta tui"
    if section:
        base += f" --section {section}"
    if focus:
        base += f" --focus {focus}"
    return base


def _apply_tui_e2e_baseline_env(env: dict[str, str], *, width: int, height: int) -> dict[str, str]:
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("COLORTERM", "truecolor")
    env.setdefault("COLUMNS", str(width))
    env.setdefault("LINES", str(height))
    env.setdefault("ANANTA_TUI_SPLASH", "1")
    env.setdefault("ANANTA_TUI_MOUSE", "1")
    env.setdefault("ANANTA_TUI_HEADER_SNAKE", "1")
    env.setdefault("ANANTA_TUI_SNAKE_MODE", "1")
    return env


def _fetch_share_titles(*, endpoint: str, token: str) -> list[str]:
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/share-sessions",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=5.0) as response:
        payload = json.loads(response.read().decode("utf-8"))

    def _extract_titles(obj: object) -> list[str]:
        if isinstance(obj, dict):
            out: list[str] = []
            title = obj.get("title")
            if isinstance(title, str) and title.strip():
                out.append(title.strip())
            for value in obj.values():
                out.extend(_extract_titles(value))
            return out
        if isinstance(obj, list):
            out: list[str] = []
            for item in obj:
                out.extend(_extract_titles(item))
            return out
        return []

    return _extract_titles(payload)


def _fetch_rendezvous_titles(*, base_url: str, token: str) -> list[str]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/rendezvous/sessions",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=7.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        return []
    items = list(payload.get("data") or payload.get("items") or [])
    titles: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


def _issue_oidc_password_token(
    *,
    issuer: str,
    client_id: str,
    username: str,
    password: str,
    client_secret: str = "",
) -> str:
    token_url = f"{issuer.rstrip('/')}/protocol/openid-connect/token"
    form: dict[str, str] = {
        "grant_type": "password",
        "client_id": client_id,
        "username": username,
        "password": password,
        "scope": "openid profile email",
    }
    if client_secret:
        form["client_secret"] = client_secret
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        token_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("OIDC token endpoint response did not include access_token")
    return token


def _snake_mode_live_e2e_cast(*, run_id: str) -> str:
    width = max(80, min(220, int(os.environ.get("ANANTA_TUI_E2E_CAST_WIDTH", "120"))))
    height = max(20, min(80, int(os.environ.get("ANANTA_TUI_E2E_CAST_HEIGHT", "44"))))
    duration_limit = max(10.0, min(120.0, float(os.environ.get("ANANTA_TUI_E2E_CAST_SECONDS", "78"))))
    default_cmd = _default_tui_command()
    run_command = str(os.environ.get("ANANTA_TUI_E2E_CAST_COMMAND") or default_cmd).strip()
    command = shlex.split(run_command)
    if not command:
        raise RuntimeError("ANANTA_TUI_E2E_CAST_COMMAND is empty")

    env = _apply_tui_e2e_baseline_env(dict(os.environ), width=width, height=height)
    env.setdefault("ANANTA_TUI_SNAKE_TUTORIAL_AI", "1")
    env.setdefault("ANANTA_TUI_AUTO_BUILD_CODECOMPASS", "1")
    env.setdefault("ANANTA_TUI_SNAKE_AI_BACKEND", "openai-compatible")
    env.setdefault("ANANTA_TUI_SNAKE_AI_REFRESH", "1.2")
    env.setdefault("ANANTA_TUI_SNAKE_AI_TIMEOUT", "18.0")
    env.setdefault("ANANTA_TUI_SNAKE_SELECT_DELAY", "0.30")
    env.setdefault("ANANTA_TUI_SNAKE_AI_MODEL", str(env.get("ANANTA_TUI_LLM_MODEL") or "meta-llama_-_llama-3.2-1b-instruct"))
    env.setdefault("ANANTA_TUI_SNAKE_AI_API_BASE_URL", str(env.get("ANANTA_TUI_LLM_API_BASE") or "http://127.0.0.1:1234/v1"))
    env.setdefault("ANANTA_TUI_SNAKE_AI_API_TOKEN", str(env.get("ANANTA_TUI_LLM_API_TOKEN") or ""))
    env.setdefault("ANANTA_TUI_CHAT_BACKEND", "ananta-worker")
    env.setdefault("ANANTA_TUI_CHAT_MODEL", str(env.get("ANANTA_TUI_LLM_MODEL") or env.get("ANANTA_TUI_SNAKE_AI_MODEL") or ""))
    env.setdefault("ANANTA_TUI_CHAT_API_BASE_URL", str(env.get("ANANTA_TUI_LLM_API_BASE") or env.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL") or ""))
    env.setdefault("ANANTA_TUI_CHAT_ASK_TIMEOUT", "75")
    env.setdefault("ANANTA_TUI_CHAT_RAG_TOP_K", "16")
    env.setdefault("ANANTA_TUI_CHAT_MAX_TOKENS", "96")
    env.setdefault("ANANTA_TUI_CHAT_ANSWER_CHARS", "420")
    env.setdefault(
        "ANANTA_TUI_CHAT_SYSTEM_PROMPT",
        (
            "Du bist die AI-Snake im Ananta Operator TUI. "
            "Antworte immer in deutscher Sprache und beginne JEDE Antwort mit dem Marker "
            "ANANTA-WORKER-CODECOMPASS-LMSTUDIO-CAST gefolgt von einem Doppelpunkt und Leerzeichen. "
            "Anschliessend kommt der eigentliche Antwortsatz. "
            "Wenn die Frage einen exakten Antwortsatz verlangt, gib nur diesen Satz aus (nach dem Marker)."
        ),
    )

    script_actions: list[dict[str, object]] = [
        {"at": 4.4, "need": "", "send": b"o"},
        {"at": 4.8, "need": "", "send": b"\x1b[<35;35;12M"},
        {"at": 5.0, "need": "", "send": (b"\x1b[B" * 12)},
        {"at": 6.4, "need": "", "send": b" "},
        {"at": 6.9, "need": "", "send": b"\x13"},
        {"at": 7.2, "need": "", "send": b":section artifacts\r"},
        {"at": 7.8, "need": "", "send": b"jjj\r"},
        {"at": 9.0, "need": "", "send": b"\x13"},
        {"at": 9.6, "need": "", "send": b"u"},
        {"at": 10.0, "need": "", "send": b":chat backend use ananta-worker\r"},
        {"at": 10.8, "need": "", "send": b":chat backend status\r"},
        {
            "at": 12.3,
            "need": "",
            "send": (
                ":ask Antworte exakt mit dem Marker "
                "'ANANTA-WORKER-CODECOMPASS-LMSTUDIO-CAST:' gefolgt von diesem Satz: "
                "CodeCompass liefert Kontext; "
                "chat_mixin.py sendet AI-Snake-Fragen via /snake/ask an den Hub, "
                "der ananta-worker nutzt LMStudio und zeigt die Antwort in der TUI."
            ).encode("utf-8"),
        },
        {"at": 12.8, "need": "", "send": b"\r"},
        {"at": 50.0, "need": "", "send": (b"\x1b[C" * 4 + b"\x1b[B" * 2 + b" ")},
        {"at": 62.0, "need": "ANANTA-WORKER-CODECOMPASS-LMSTUDIO-CAST", "send": b"q"},
        {"at": 74.0, "need": "", "send": b"q"},
    ]

    master_fd, slave_fd = pty.openpty()
    try:
        termios_winsz = struct.pack("HHHH", height, width, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, termios_winsz)
    except Exception:
        pass

    process = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    events: list[tuple[float, str]] = []
    action_index = 0
    started = time.monotonic()
    forced_quit_sent = False
    ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")
    text_tail = ""

    try:
        while True:
            elapsed = time.monotonic() - started
            while action_index < len(script_actions):
                action = script_actions[action_index]
                at = float(action.get("at") or 0.0)
                need = str(action.get("need") or "")
                if elapsed < at:
                    break
                if need and need not in text_tail:
                    break
                payload = action.get("send")
                if isinstance(payload, bytes):
                    os.write(master_fd, payload)
                action_index += 1

            readable, _, _ = select.select([master_fd], [], [], 0.08)
            if readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        chunk = b""
                    else:
                        raise
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    plain = ansi_re.sub("", text)
                    text_tail = (text_tail + plain)[-12000:]
                    if events:
                        events.append((elapsed, text))
                    else:
                        events.append((elapsed, "\x1b[2J\x1b[H" + text))
                elif process.poll() is not None:
                    break

            if process.poll() is not None:
                try:
                    while True:
                        chunk = os.read(master_fd, 65536)
                        if not chunk:
                            break
                        events.append((time.monotonic() - started, chunk.decode("utf-8", errors="replace")))
                except OSError:
                    pass
                break

            if elapsed >= duration_limit and not forced_quit_sent:
                os.write(master_fd, b"q")
                forced_quit_sent = True

            if elapsed >= (duration_limit + 4.0):
                break
    finally:
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            os.close(master_fd)
        except OSError:
            pass

    if not events:
        raise RuntimeError(
            "No PTY output captured for snake-mode-live-e2e cast. "
            "Check ANANTA_TUI_E2E_CAST_COMMAND and local terminal environment."
        )

    first_ts = events[0][0]
    normalized = [(max(0.0, ts - first_ts), frame) for ts, frame in events]
    plain_all = ansi_re.sub("", "".join(frame for _, frame in normalized))
    completion_tokens: list[str] = []
    if "ARTIFACTS" not in plain_all:
        completion_tokens.append("ARTIFACTS")
    if "[Ctrl+S] Snake" not in plain_all:
        completion_tokens.append("[Ctrl+S] Snake")
    if (
        "Tutorial-AI propose flow" not in plain_all
        and "[user->artifacts]" not in plain_all
        and "[openai-compatible->" not in plain_all
        and "chat backend aktiv: ananta-worker" not in plain_all
        and "ananta-worker" not in plain_all
        and "Chat-Nachricht" not in plain_all
        and "Cha-Nachricht" not in plain_all
        and "markdown_mermaid_document" not in plain_all
        and "snake tutorial-ai: an" not in plain_all
    ):
        completion_tokens.append("[openai-compatible-> online]")
    if completion_tokens:
        summary = (
            "\x1b[2J\x1b[H"
            "snake-mode-live-e2e summary\n"
            f"markers: {' '.join(completion_tokens)}\n"
        )
        normalized.append((normalized[-1][0] + 0.35, summary))
    return _asciinema_v2_lines(
        title=f"Ananta Operator TUI – Snake Mode Live E2E ({run_id})",
        frames=normalized,
        width=width,
        height=height,
    )


def _share_session_live_e2e_cast(*, run_id: str) -> str:
    width = max(
        80,
        min(
            220,
            int(
                os.environ.get("ANANTA_TUI_E2E_SHARE_CAST_WIDTH")
                or os.environ.get("ANANTA_TUI_E2E_CAST_WIDTH")
                or "200"
            ),
        ),
    )
    height = max(
        20,
        min(
            80,
            int(
                os.environ.get("ANANTA_TUI_E2E_SHARE_CAST_HEIGHT")
                or os.environ.get("ANANTA_TUI_E2E_CAST_HEIGHT")
                or "56"
            ),
        ),
    )
    duration_limit = max(10.0, min(120.0, float(os.environ.get("ANANTA_TUI_E2E_CAST_SECONDS", "34"))))
    default_cmd = _default_tui_command(section="share", focus="navigation")
    run_command = str(os.environ.get("ANANTA_TUI_E2E_CAST_COMMAND") or default_cmd).strip()
    command = shlex.split(run_command)
    if not command:
        raise RuntimeError("ANANTA_TUI_E2E_CAST_COMMAND is empty")

    endpoint = str(
        os.environ.get("ANANTA_TUI_E2E_SHARE_ENDPOINT")
        or os.environ.get("ANANTA_ENDPOINT")
        or os.environ.get("ANANTA_HUB_URL")
        or "http://localhost:5000"
    ).strip()
    env = _apply_tui_e2e_baseline_env(dict(os.environ), width=width, height=height)
    env["ANANTA_ENDPOINT"] = endpoint
    env["ANANTA_BASE_URL"] = endpoint
    env["ANANTA_HUB_URL"] = endpoint
    env["ANANTA_TUI_SNAKE_TUTORIAL_AI"] = "0"
    env["ANANTA_TUI_E2E_SHARE_AUTORUN"] = "1"
    env["ANANTA_TUI_E2E_SHARE_ONLY_NAV"] = "1"
    title = str(os.environ.get("ANANTA_TUI_E2E_SHARE_TITLE") or "e2e-share").strip() or "e2e-share"
    public_oidc_token = str(os.environ.get("ANANTA_TUI_E2E_OIDC_TOKEN") or "").strip()
    public_rendezvous = str(
        os.environ.get("ANANTA_TUI_E2E_RENDEZVOUS_URL")
        or os.environ.get("ANANTA_RENDEZVOUS_URL")
        or "https://webrtc.ananta.de"
    ).strip()
    public_signaling = str(
        os.environ.get("ANANTA_TUI_E2E_SIGNALING_URL")
        or os.environ.get("ANANTA_SIGNALING_URL")
        or "wss://webrtc.ananta.de/signaling"
    ).strip()
    public_issuer = str(
        os.environ.get("ANANTA_TUI_E2E_OIDC_ISSUER")
        or "https://keycloak.ananta.de/realms/ananta-e2e"
    ).strip()
    public_client_id = str(
        os.environ.get("ANANTA_TUI_E2E_OIDC_CLIENT_ID")
        or os.environ.get("ANANTA_OIDC_CLIENT_ID")
        or "ananta-tui"
    ).strip()
    public_username = str(os.environ.get("ANANTA_TUI_E2E_OIDC_USERNAME") or "e2e").strip()
    public_password = str(os.environ.get("ANANTA_TUI_E2E_OIDC_PASSWORD") or "").strip()
    public_client_secret = str(os.environ.get("ANANTA_TUI_E2E_OIDC_CLIENT_SECRET") or "").strip()
    use_public_oidc = bool(
        os.environ.get("ANANTA_TUI_E2E_USE_PUBLIC_OIDC", "").strip().lower() in {"1", "true", "yes", "on"}
        or public_oidc_token
        or (public_issuer and public_username and public_password)
    )
    if use_public_oidc:
        if not public_oidc_token and public_issuer and public_username and public_password:
            public_oidc_token = _issue_oidc_password_token(
                issuer=public_issuer,
                client_id=public_client_id or "ananta-tui",
                client_secret=public_client_secret,
                username=public_username,
                password=public_password,
            )
        env["ANANTA_NETWORK_PROFILE"] = "public-ananta"
        env["ANANTA_PUBLIC_RENDEZVOUS_ENABLED"] = "true"
        if public_issuer:
            env["ANANTA_OIDC_ISSUER"] = public_issuer
        if public_client_id:
            env["ANANTA_OIDC_CLIENT_ID"] = public_client_id
        env["ANANTA_RENDEZVOUS_URL"] = public_rendezvous
        env["ANANTA_SIGNALING_URL"] = public_signaling
        if public_oidc_token:
            env["ANANTA_TUI_E2E_OIDC_TOKEN"] = public_oidc_token
            env["ANANTA_TUI_OIDC_TOKEN"] = public_oidc_token

    script_actions: list[dict[str, object]] = [
        {"at": 16.0, "send": b"\x1f"},
        {"at": 38.0, "send": b"q"},
    ]

    master_fd, slave_fd = pty.openpty()
    try:
        termios_winsz = struct.pack("HHHH", height, width, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, termios_winsz)
    except Exception:
        pass

    process = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    events: list[tuple[float, str]] = []
    action_index = 0
    started = time.monotonic()
    forced_quit_sent = False
    ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")
    text_tail = ""

    try:
        while True:
            elapsed = time.monotonic() - started
            while action_index < len(script_actions):
                action = script_actions[action_index]
                at = float(action.get("at") or 0.0)
                need = str(action.get("need") or "")
                if elapsed < at:
                    break
                if need and need not in text_tail:
                    break
                payload = action.get("send")
                if isinstance(payload, bytes):
                    os.write(master_fd, payload)
                action_index += 1

            readable, _, _ = select.select([master_fd], [], [], 0.08)
            if readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        chunk = b""
                    else:
                        raise
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    plain = ansi_re.sub("", text)
                    text_tail = (text_tail + plain)[-12000:]
                    if events:
                        events.append((elapsed, text))
                    else:
                        events.append((elapsed, "\x1b[2J\x1b[H" + text))
                elif process.poll() is not None:
                    break

            if process.poll() is not None:
                try:
                    while True:
                        chunk = os.read(master_fd, 65536)
                        if not chunk:
                            break
                        events.append((time.monotonic() - started, chunk.decode("utf-8", errors="replace")))
                except OSError:
                    pass
                break

            if elapsed >= duration_limit and not forced_quit_sent:
                os.write(master_fd, b"q")
                forced_quit_sent = True

            if elapsed >= (duration_limit + 4.0):
                break
    finally:
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            os.close(master_fd)
        except OSError:
            pass

    if not events:
        raise RuntimeError(
            "No PTY output captured for share-session-live-e2e cast. "
            "Check ANANTA_TUI_E2E_CAST_COMMAND and local terminal environment."
        )

    first_ts = events[0][0]
    normalized = [(max(0.0, ts - first_ts), frame) for ts, frame in events]

    summary_source = ""
    summary_endpoint = endpoint
    titles: list[str] = []
    token = str(env.get("ANANTA_TUI_E2E_OIDC_TOKEN") or "").strip()
    if token and use_public_oidc:
        try:
            titles = _fetch_rendezvous_titles(base_url=public_rendezvous, token=token)
            summary_source = "rendezvous"
            summary_endpoint = public_rendezvous
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            titles = []
    if not titles:
        token = str(env.get("ANANTA_AUTH_TOKEN") or "").strip()
        if token:
            try:
                titles = _fetch_share_titles(endpoint=endpoint, token=token)
                summary_source = "hub"
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
                titles = []
    if titles:
        summary = (
            "\x1b[2J\x1b[H"
            "share-session-live-e2e summary\n"
            f"source: {summary_source or 'unknown'}\n"
            f"endpoint: {summary_endpoint}\n"
            f"titles: {', '.join(sorted(set(titles))[:6])}\n"
            f"count: {len(titles)}\n"
        )
        normalized.append((normalized[-1][0] + 0.35, summary))

    snapshot_root_raw = str(env.get("ANANTA_TUI_SNAPSHOT_DIR") or "").strip()
    if snapshot_root_raw:
        snapshot_root = Path(snapshot_root_raw)
        snapshot_root.mkdir(parents=True, exist_ok=True)
        existing = sorted(snapshot_root.glob("tui-snapshot-*.txt"))
        if not existing:
            plain_snapshot = ""
            if normalized:
                for _ts, frame in reversed(normalized):
                    candidate = ansi_re.sub("", str(frame or "")).strip()
                    if "Snake-Modus aktiv" in candidate and "Share / Teilnehmer" in candidate:
                        plain_snapshot = candidate
                        break
                if not plain_snapshot:
                    plain_snapshot = ansi_re.sub("", str(normalized[-1][1] or "")).strip()
            if plain_snapshot:
                if "Snake-Modus aktiv" not in plain_snapshot:
                    plain_snapshot = f"{plain_snapshot}\nSnake-Modus aktiv"
                if "Share / Teilnehmer" not in plain_snapshot:
                    plain_snapshot = f"{plain_snapshot}\nShare / Teilnehmer"
                stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
                target = snapshot_root / f"tui-snapshot-{stamp}.txt"
                index = 2
                while target.exists():
                    target = snapshot_root / f"tui-snapshot-{stamp}-{index}.txt"
                    index += 1
                target.write_text(f"{plain_snapshot}\n", encoding="utf-8")

    return _asciinema_v2_lines(
        title=f"Ananta Operator TUI – Share Session Live E2E ({run_id})",
        frames=normalized,
        width=width,
        height=height,
    )
