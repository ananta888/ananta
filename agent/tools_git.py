from typing import Optional

from flask import current_app, g, has_request_context

from agent.tools_registry import registry


def _check_git_access(operation: str = "read") -> tuple[bool, str]:
    from agent.services.platform_governance_service import get_platform_governance_service
    gov = get_platform_governance_service()
    cfg = current_app.config.get("AGENT_CONFIG")
    if not gov.evaluate_action_pack_access("git", cfg):
        return False, "Action Pack 'git' ist deaktiviert."
    return True, ""


def _git_cwd() -> str | None:
    try:
        if has_request_context():
            wd = g.get("workspace_dir")
            if wd:
                return str(wd)
    except Exception:
        pass
    return None


@registry.register(
    name="git_status",
    description="Zeigt den aktuellen Git-Status.",
    parameters={"type": "object", "properties": {}},
)
def git_status_tool():
    ok, err = _check_git_access("read")
    if not ok: return {"error": err}

    import subprocess
    try:
        res = subprocess.run(["git", "status"], capture_output=True, text=True, timeout=10, cwd=_git_cwd())
        return {"output": res.stdout}
    except Exception as e:
        return {"error": f"Git-Fehler: {e}"}


@registry.register(
    name="git_diff",
    description="Zeigt Diffs von Aenderungen.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Optionaler Pfadfilter"},
            "cached": {"type": "boolean", "description": "Gestagete Aenderungen zeigen", "default": False},
        },
    },
)
def git_diff_tool(path: Optional[str] = None, cached: bool = False):
    ok, err = _check_git_access("read")
    if not ok: return {"error": err}

    import subprocess
    cmd = ["git", "diff"]
    if cached: cmd.append("--cached")
    if path: cmd.append(path)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=_git_cwd())
        return {"output": res.stdout}
    except Exception as e:
        return {"error": f"Git-Fehler: {e}"}


@registry.register(
    name="git_log",
    description="Zeigt die Commit-Historie.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Anzahl der Commits", "default": 10},
        },
    },
)
def git_log_tool(limit: int = 10):
    ok, err = _check_git_access("read")
    if not ok: return {"error": err}

    import subprocess
    try:
        res = subprocess.run(["git", "log", "-n", str(limit), "--oneline"], capture_output=True, text=True, timeout=10, cwd=_git_cwd())
        return {"output": res.stdout}
    except Exception as e:
        return {"error": f"Git-Fehler: {e}"}


@registry.register(
    name="git_commit",
    description="Erstellt einen Commit mit den aktuell gestageten Aenderungen.",
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit-Nachricht"},
        },
        "required": ["message"],
    },
)
def git_commit_tool(message: str):
    ok, err = _check_git_access("write")
    if not ok: return {"error": err}

    try:
        from agent.services.commit_message_validator import CommitMessageValidator
        result = CommitMessageValidator().validate(message)
        if not result.valid:
            return {"error": "invalid_commit_message", "details": result.errors}
    except Exception:
        pass

    import subprocess
    try:
        res = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True, timeout=10, cwd=_git_cwd())
        if res.returncode != 0:
            return {"error": f"Commit fehlgeschlagen: {res.stderr}"}

        from agent.common.audit import log_audit
        log_audit("git_commit", {"message": message})
        return {"status": "success", "output": res.stdout}
    except Exception as e:
        return {"error": f"Git-Fehler: {e}"}


@registry.register(
    name="git_push",
    description="Pusht den aktuellen Branch auf das konfigurierte Remote-Repository.",
    parameters={"type": "object", "properties": {}},
)
def git_push_tool():
    ok, err = _check_git_access("write")
    if not ok: return {"error": err}

    try:
        git_ctx = g.get("git_context") if has_request_context() else None
    except Exception:
        git_ctx = None

    remote_url = getattr(git_ctx, "remote_url", None) if git_ctx else None
    if not remote_url:
        return {"error": "no_remote_configured", "hint": "Set git_workspace.remote_url in goal config"}

    branch = getattr(git_ctx, "branch", "HEAD")

    import subprocess
    try:
        res = subprocess.run(
            ["git", "push", "origin", branch],
            capture_output=True, text=True, timeout=30, cwd=_git_cwd()
        )
        from agent.common.audit import log_audit
        log_audit("git_push", {"branch": branch, "remote_url": remote_url, "returncode": res.returncode})
        if res.returncode != 0:
            return {"error": f"Push fehlgeschlagen: {res.stderr}"}
        return {"status": "success", "output": res.stdout, "branch": branch}
    except Exception as e:
        return {"error": f"Git-Fehler: {e}"}
