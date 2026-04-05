from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from typing import Any

from agent.config import settings
from agent.services.cli_session_service import get_cli_session_service


class OpencodeRuntimeService:
    """Manages persistent opencode serve runtimes and native sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._servers: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _pick_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            return int(sock.getsockname()[1])

    @staticmethod
    def _http_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: int = 10) -> Any:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
        if not raw:
            return None
        return json.loads(raw)

    @staticmethod
    def _extract_error_message(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        error = info.get("error") if isinstance(info.get("error"), dict) else {}
        data = error.get("data") if isinstance(error.get("data"), dict) else {}
        if data.get("message"):
            return str(data.get("message"))
        if error.get("message"):
            return str(error.get("message"))
        return ""

    @staticmethod
    def _collect_text_parts(parts: list[dict] | None) -> str:
        texts: list[str] = []
        for part in list(parts or []):
            if not isinstance(part, dict):
                continue
            text = str(part.get("text") or "").strip()
            if text:
                texts.append(text)
        return "\n".join(texts).strip()

    @staticmethod
    def _extract_message_text(payload: Any) -> str:
        if isinstance(payload, dict):
            text = OpencodeRuntimeService._collect_text_parts(payload.get("parts"))
            if text:
                return text
        if isinstance(payload, list):
            for entry in reversed(payload):
                if not isinstance(entry, dict):
                    continue
                info = entry.get("info") if isinstance(entry.get("info"), dict) else {}
                if str(info.get("role") or "").strip().lower() != "assistant":
                    continue
                text = OpencodeRuntimeService._collect_text_parts(entry.get("parts"))
                if text:
                    return text
        return ""

    @staticmethod
    def _server_scope_key(session: dict, runtime_cfg: dict[str, Any]) -> str:
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        scope_key = str(metadata.get("scope_key") or "").strip()
        if not scope_key:
            scope_key = str(session.get("conversation_id") or session.get("id") or "opencode").strip()
        model = str(runtime_cfg.get("model") or "").strip()
        return f"{scope_key}::{model}"

    @staticmethod
    def _toolless_agent_name() -> str:
        return "ananta-worker"

    @staticmethod
    def _public_server_payload(payload: dict[str, Any]) -> dict[str, Any]:
        public = dict(payload or {})
        process = public.pop("process", None)
        if process is not None:
            public["pid"] = getattr(process, "pid", None)
        return public

    @classmethod
    def _build_server_config(cls, runtime_cfg: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        from agent.common.sgpt import _build_opencode_theless_agent_config

        config = deepcopy(runtime_cfg.get("provider_config") or {})
        config.setdefault("$schema", "https://opencode.ai/config.json")
        config.setdefault("provider", {})
        config.setdefault("agent", {})
        config.setdefault("mode", {})
        config.setdefault("plugin", [])
        config.setdefault("command", {})

        model_name = str(runtime_cfg.get("model") or "").strip()
        if model_name:
            config["model"] = model_name
            config["small_model"] = model_name

        agent_name = None
        if str(runtime_cfg.get("target_provider") or "").strip().lower() == "ollama":
            agent_name = cls._toolless_agent_name()
            config.setdefault("agent", {})[agent_name] = _build_opencode_theless_agent_config()
            config["default_agent"] = agent_name

        return config, agent_name

    def _ensure_server(self, session: dict, runtime_cfg: dict[str, Any]) -> dict[str, Any]:
        server_key = self._server_scope_key(session, runtime_cfg)
        with self._lock:
            existing = self._servers.get(server_key)
            if existing and existing.get("process") and existing["process"].poll() is None:
                return self._public_server_payload(existing)

        opencode_bin = settings.opencode_path or "opencode"
        opencode_resolved = shutil.which(opencode_bin)
        if opencode_resolved is None:
            raise RuntimeError(f"OpenCode binary '{opencode_bin}' not found")

        port = self._pick_free_port()
        server_cfg, agent_name = self._build_server_config(runtime_cfg)
        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(server_cfg, ensure_ascii=True)

        process = subprocess.Popen(  # noqa: S603 - executable resolved via shutil.which
            [opencode_resolved, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )
        server_url = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        last_error = ""
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"opencode_serve_exited:{process.returncode}")
            try:
                health = self._http_json(f"{server_url}/global/health", timeout=2)
                if isinstance(health, dict) and health.get("healthy") is True:
                    break
            except Exception as exc:  # pragma: no cover - transient startup
                last_error = str(exc)
            try:
                config = self._http_json(f"{server_url}/config", timeout=2)
                if isinstance(config, dict):
                    break
            except Exception as exc:  # pragma: no cover - transient startup
                last_error = str(exc)
            time.sleep(0.25)
        else:
            process.terminate()
            raise RuntimeError(f"opencode_serve_start_timeout:{last_error}")

        payload = {
            "server_key": server_key,
            "server_url": server_url,
            "port": port,
            "model": runtime_cfg.get("model"),
            "agent": agent_name,
            "process": process,
            "started_at": time.time(),
            "updated_at": time.time(),
        }
        with self._lock:
            self._servers[server_key] = payload
        return self._public_server_payload(payload)

    def ensure_session_runtime(self, session: dict, *, model: str | None = None) -> dict[str, Any]:
        from agent.common.sgpt import resolve_opencode_runtime_config

        runtime_cfg = resolve_opencode_runtime_config(model=model or session.get("model"))
        server = self._ensure_server(session, runtime_cfg)
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        runtime_meta = metadata.get("opencode_runtime") if isinstance(metadata.get("opencode_runtime"), dict) else {}
        if (
            runtime_meta
            and str(runtime_meta.get("server_key") or "") == str(server.get("server_key") or "")
            and str(runtime_meta.get("native_session_id") or "").strip()
        ):
            return deepcopy(runtime_meta)

        title = str(metadata.get("scope_key") or session.get("conversation_id") or session.get("id") or "OpenCode Session").strip()
        created = self._http_json(
            f"{server['server_url']}/session",
            method="POST",
            body={"title": title},
            timeout=10,
        )
        native_session_id = str((created or {}).get("id") or "").strip()
        if not native_session_id:
            raise RuntimeError("opencode_native_session_create_failed")

        runtime_meta = {
            "kind": "native_server",
            "server_key": server["server_key"],
            "server_url": server["server_url"],
            "native_session_id": native_session_id,
            "agent": server.get("agent"),
            "model": runtime_cfg.get("model"),
            "updated_at": time.time(),
        }
        updated = get_cli_session_service().update_session(
            str(session.get("id") or ""),
            model=str(runtime_cfg.get("model") or "").strip() or None,
            metadata_updates={"opencode_runtime": runtime_meta},
        )
        if updated:
            runtime_meta = ((updated.get("metadata") or {}).get("opencode_runtime") or runtime_meta)
        return deepcopy(runtime_meta)

    def run_session_turn(self, session: dict, *, prompt: str, timeout: int = 60, model: str | None = None) -> tuple[int, str, str]:
        runtime_meta = self.ensure_session_runtime(session, model=model)
        session_id = str(runtime_meta.get("native_session_id") or "").strip()
        server_url = str(runtime_meta.get("server_url") or "").strip()
        if not session_id or not server_url:
            return -1, "", "opencode_native_session_missing"

        payload: dict[str, Any] = {"parts": [{"type": "text", "text": str(prompt or "")}]}
        if runtime_meta.get("agent"):
            payload["agent"] = runtime_meta["agent"]
        try:
            response = self._http_json(
                f"{server_url}/session/{session_id}/message",
                method="POST",
                body=payload,
                timeout=max(timeout, 10),
            )
            output = self._extract_message_text(response)
            if not output:
                history = self._http_json(f"{server_url}/session/{session_id}/message", timeout=10)
                output = self._extract_message_text(history)
            err = self._extract_error_message(response)
            if err and not output:
                return -1, "", err
            return 0, output, err
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            return -1, "", detail or str(exc)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            logging.exception("Native OpenCode session turn failed: %s", exc)
            return -1, "", str(exc)


opencode_runtime_service = OpencodeRuntimeService()


def get_opencode_runtime_service() -> OpencodeRuntimeService:
    return opencode_runtime_service
