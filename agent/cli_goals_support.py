"""Hub I/O, terminal output and parsing helpers for the goals CLI.

The goals CLI command modules (query, mutation, planning, repair) receive
their collaborators explicitly through :class:`CliGoalsDependencies` instead
of resolving them from the ``agent.cli_goals`` facade at call time. Tests
build a ``CliGoalsDependencies`` with fakes; production uses
:data:`DEFAULT_DEPENDENCIES`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import requests

from agent.config import settings
from agent.tui_contract import sanitize_terminal_text

SHORTCUT_GOALS = {
    "ask": {
        "mode": None,
        "prefix": "Beantworte diese Frage und nenne bei Unsicherheit die naechsten pruefbaren Schritte:",
        "context": "Kurzkommando: Frage. Fokus auf klare Antwort, Annahmen und naechste pruefbare Schritte.",
    },
    "plan": {
        "mode": None,
        "prefix": "Plane konkrete naechste Schritte fuer:",
        "context": "Kurzkommando: Planen. Fokus auf Ziel, Aufgaben, Reihenfolge und Pruefung.",
    },
    "analyze": {
        "mode": "repo_analysis",
        "prefix": "Analysiere und fasse die wichtigsten Befunde zusammen:",
        "context": "Kurzkommando: Analyse. Fokus auf Verstaendnis, Risiken und naechste Schritte.",
    },
    "review": {
        "mode": "code_review",
        "prefix": "Fuehre ein Review durch und priorisiere konkrete Risiken:",
        "context": "Kurzkommando: Review. Fokus auf Bugs, Regressionen, Tests und klare Findings.",
    },
    "diagnose": {
        "mode": "docker_compose_repair",
        "prefix": "Diagnostiziere das Problem und schlage eine robuste Start- oder Reparatursequenz vor:",
        "context": "Kurzkommando: Diagnose. Fokus auf Logs, Compose, Ports, Health-Checks und naechste Pruefung.",
    },
    "patch": {
        "mode": "code_fix",
        "prefix": "Plane einen kleinen, testbaren Patch fuer:",
        "context": "Kurzkommando: Patch. Fokus auf kleine Aenderung, Regressionstest und minimale Nebenwirkungen.",
    },
    "new-project": {
        "mode": "new_software_project",
        "prefix": "Lege ein neues Softwareprojekt kontrolliert an aus dieser Idee:",
        "context": "Kurzkommando: Neues Projekt. Fokus auf Scope, Architekturvorschlag, initiales Backlog, Tests und sichere Defaults.",
    },
    "evolve-project": {
        "mode": "project_evolution",
        "prefix": "Plane eine kontrollierte Weiterentwicklung fuer ein bestehendes Projekt:",
        "context": "Kurzkommando: Projekt weiterentwickeln. Fokus auf betroffene Bereiche, Risiken, Tests und kleine reviewbare Schritte.",
    },
    "repair-admin": {
        "mode": "admin_repair",
        "prefix": "Plane eine bounded Admin-Reparatur als Shared Foundation fuer:",
        "context": "Kurzkommando: Admin Repair. Fokus auf bounded evidence, dry-run-first, advisory Klassifikation und verifizierbare Repair-Schritte.",
    },
}

_CONTAINER_WORKSPACE_ROOT = "/project-workspaces"
_HOST_WORKSPACE_ROOT = "./project-workspaces"
_HOST_COMMAND_TIMEOUT_SECONDS = 5


# ── terminal output ──────────────────────────────────────────────────────────


def terminal_text(value, *, max_chars: int = 240) -> str:
    return sanitize_terminal_text(value, max_chars=max_chars)


def print_terminal(template: str, *values) -> None:
    print(template.format(*(terminal_text(value) for value in values)))


def next_step_for_status(status_code: int, message: str | None = None) -> str:
    text = str(message or "").lower()
    if status_code in {401, 403}:
        return "check ANANTA_USER/ANANTA_PASSWORD and governance permissions."
    if status_code == 404:
        return "check that the hub version exposes this endpoint and that ANANTA_BASE_URL points to the hub."
    if status_code == 409 or "policy" in text or "governance" in text or "blocked" in text:
        return "review the governance mode or narrow the goal before retrying."
    if status_code >= 500:
        return "check hub logs, then run `ananta status` after the hub is healthy."
    return "retry with a narrower goal or run `ananta status` for readiness."


def read_json(response: requests.Response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {}


def api_data(response: requests.Response):
    payload = read_json(response)
    if isinstance(payload, dict):
        return payload.get("data", payload)
    return {}


def print_error(response: requests.Response):
    payload = read_json(response)
    message = payload.get("message") if isinstance(payload, dict) else None
    if message:
        print_terminal("Error: {} - {}", response.status_code, message)
    else:
        print_terminal("Error: {} - {}", response.status_code, response.text)
    print_terminal("Next step: {}", next_step_for_status(response.status_code, message or response.text))


# ── hub connection ───────────────────────────────────────────────────────────


def get_base_url():
    configured = os.environ.get("ANANTA_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return f"http://localhost:{settings.port}"


def get_auth_token(base_url: str) -> str:
    username = os.environ.get("ANANTA_USER") or os.environ.get("INITIAL_ADMIN_USER") or "admin"
    password = os.environ.get("ANANTA_PASSWORD") or os.environ.get("INITIAL_ADMIN_PASSWORD") or "admin"

    try:
        response = requests.post(f"{base_url}/login", json={"username": username, "password": password}, timeout=10)
    except requests.RequestException as exc:
        print_terminal("Error: Hub not reachable at {}", base_url)
        print_terminal("Next step: start the hub or set ANANTA_BASE_URL. Details: {}", str(exc))
        sys.exit(1)

    if response.status_code != 200:
        print_terminal("Error: Login failed - {}", response.status_code)
        print("Next step: check ANANTA_USER/ANANTA_PASSWORD or reset the local admin password.")
        sys.exit(1)

    data = response.json().get("data", {})
    return data.get("access_token", "")


class HubRequest(Protocol):
    """Port for authenticated hub HTTP calls used by the CLI commands."""

    def __call__(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        params: dict | None = None,
        timeout: int = 30,
    ) -> Any: ...


@dataclass(frozen=True)
class HubHttpClient:
    """Authenticated HTTP adapter for the hub API.

    ``transport`` defaults to ``requests.request`` (resolved per call).
    """

    base_url_provider: Callable[[], str] = get_base_url
    token_provider: Callable[[str], str] = get_auth_token
    transport: Callable[..., Any] | None = None

    def __call__(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        params: dict | None = None,
        timeout: int = 30,
    ):
        base_url = self.base_url_provider()
        token = self.token_provider(base_url)
        transport = self.transport or requests.request
        try:
            return transport(
                method=method,
                url=f"{base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
                params=params,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            print_terminal("Error: Hub request failed for {}", path)
            print_terminal("Next step: run `ananta first-run` and verify ANANTA_BASE_URL. Details: {}", str(exc))
            sys.exit(1)


hub_request = HubHttpClient()


def run_host_command(cmd: str) -> str:
    """Run a read-only diagnostic shell command on the local host."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=_HOST_COMMAND_TIMEOUT_SECONDS)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        return (out + ("\n" + err if err and not out else "")).strip()
    except Exception:
        return ""


@dataclass(frozen=True)
class CliGoalsDependencies:
    """Explicit collaborators of the goals CLI commands."""

    request: HubRequest = hub_request
    api_data: Callable[[Any], Any] = api_data
    base_url: Callable[[], str] = get_base_url
    run_host_command: Callable[[str], str] = run_host_command


DEFAULT_DEPENDENCIES = CliGoalsDependencies()


# ── argument parsing helpers ─────────────────────────────────────────────────


def _mirror_absolute_output_dir(raw: str) -> tuple[str, str | None]:
    """Map an arbitrary absolute host path to /project-workspaces/external and mirror via symlink."""
    host_requested = Path(raw)
    host_ws_root = Path(_HOST_WORKSPACE_ROOT).resolve()
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw.strip("/")).strip("-.") or "workspace"
    container = f"{_CONTAINER_WORKSPACE_ROOT}/external/{slug}"
    host_backing = host_ws_root / "external" / slug
    try:
        host_backing.mkdir(parents=True, exist_ok=True)
        host_requested.parent.mkdir(parents=True, exist_ok=True)
        if host_requested.exists() or host_requested.is_symlink():
            if host_requested.is_symlink():
                current_target = host_requested.resolve(strict=False)
                if current_target != host_backing.resolve():
                    return container, str(host_backing)
            elif host_requested.is_dir():
                # Keep existing real directories untouched to avoid destructive behavior.
                return container, str(host_requested)
            else:
                return container, str(host_backing)
        else:
            host_requested.symlink_to(host_backing, target_is_directory=True)
    except Exception:
        return container, str(host_backing)
    return container, str(host_requested)


def resolve_output_dir(output_dir: str) -> tuple[str, str | None]:
    """Translate a user-supplied output_dir to (container_path, host_display_path).

    Rules:
    - Bare name or relative path  → /project-workspaces/<name>
    - Absolute /project-workspaces/…  → kept as-is
    - Other absolute path  → mapped to /project-workspaces/external/<slug>
    """
    raw = output_dir.strip()
    if not raw:
        return raw, None
    if raw.startswith(_CONTAINER_WORKSPACE_ROOT + "/") or raw == _CONTAINER_WORKSPACE_ROOT:
        rel = raw[len(_CONTAINER_WORKSPACE_ROOT):].lstrip("/")
        host = f"{_HOST_WORKSPACE_ROOT}/{rel}" if rel else _HOST_WORKSPACE_ROOT
        return raw, host
    if not os.path.isabs(raw):
        name = raw.removeprefix("./").replace("\\", "/").strip("/") or raw
        return f"{_CONTAINER_WORKSPACE_ROOT}/{name}", f"{_HOST_WORKSPACE_ROOT}/{name}"
    return _mirror_absolute_output_dir(raw)


def parse_rag_sources(raw: str) -> dict:
    """Parse comma-separated RAG source tokens into a rag_sources dict.

    Token formats:
      col:<id>  or bare <id>  → knowledge_collection_ids
      art:<id>                → artifact_ids
      path:<rel-path>         → repo_scope_refs
    """
    if not raw:
        return {}
    collection_ids: list[str] = []
    artifact_ids: list[str] = []
    repo_scope_refs: list[dict] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token.startswith("art:"):
            artifact_ids.append(token[4:].strip())
        elif token.startswith("path:"):
            repo_scope_refs.append({"path": token[5:].strip()})
        else:
            collection_ids.append(token.removeprefix("col:").strip())
    result: dict = {}
    if collection_ids:
        result["knowledge_collection_ids"] = collection_ids
    if artifact_ids:
        result["artifact_ids"] = artifact_ids
    if repo_scope_refs:
        result["repo_scope_refs"] = repo_scope_refs
    return result


def planning_mode_to_use_template(planning_mode: str | None) -> bool | None:
    """Map --planning-mode flag to use_template API field."""
    if not planning_mode:
        return None
    m = planning_mode.strip().lower()
    if m == "llm":
        return False
    if m in {"template", "auto"}:
        return True
    return None


def parse_mode_data(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON for --mode-data ({exc})")
        sys.exit(2)
    if not isinstance(parsed, dict):
        print("Error: --mode-data must be a JSON object")
        sys.exit(2)
    return parsed
