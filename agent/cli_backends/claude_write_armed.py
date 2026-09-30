"""Write-armed Claude Code runs in an isolated workspace and the reviewed-diff apply step.

The Claude CLI entry points in :mod:`agent.cli_backends.opencode` stay the public API; they
pass their runtime configuration, settings, binary resolution and backend permit in through
explicit keyword-only parameters, so this module owns only the isolated-workspace and
diff-review workflow (SRP) and depends on injected collaborators (DIP).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

WRITE_ARMED_GIT_IDENTITY = ["-c", "user.email=ananta@local", "-c", "user.name=ananta-write-armed"]
WRITE_ARMED_MAX_DIFF_CHARS = 200_000


def ignore_transient_git_locks(directory: str, names: list[str]) -> set[str]:
    """``copytree`` ignore hook: git's ``*.lock`` files inside ``.git`` are transient (a concurrent git
    process, e.g. auto-maintenance, creates and removes them) and would block git in the copy; project files
    named ``*.lock`` outside ``.git`` are copied as usual."""
    if ".git" not in Path(directory).parts:
        return set()
    return {name for name in names if name.endswith(".lock")}


def run_git(args: list[str], cwd: str, timeout: int = 60, input_text: str | None = None) -> tuple[int, str, str]:
    """Hilfsroutine fuer git-Aufrufe im write_armed-Workspace."""
    git_bin = shutil.which("git")
    if git_bin is None:
        return -1, "", "git binary not found"
    try:
        result = subprocess.run(  # noqa: S603 - git via shutil.which, args list-only
            [git_bin, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout,
            input=input_text,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "git timeout"
    except Exception as exc:  # pragma: no cover - defensive
        return -1, "", str(exc)


def _allowed_git_workdir_error(workdir: str | None, runtime_cfg: dict, *, action: str, git_hint: str) -> str | None:
    """Return the gate error for ``workdir`` or ``None`` when it is an allowed Git repository."""
    if not runtime_cfg["enabled"]:
        return "Claude CLI backend ist deaktiviert (claude_cli.enabled=false)."
    if not runtime_cfg["allowed_paths"]:
        return f"{action} erfordert konfigurierte claude_cli.allowed_paths (bewusstes Opt-in pro Workspace)."
    if not workdir:
        return f"{action} erfordert ein explizites workdir."
    workdir_abs = os.path.realpath(workdir)
    if not any(
        workdir_abs == os.path.realpath(p) or workdir_abs.startswith(os.path.realpath(p) + os.sep)
        for p in runtime_cfg["allowed_paths"]
    ):
        return f"Workdir '{workdir}' liegt ausserhalb von claude_cli.allowed_paths"
    if not os.path.isdir(os.path.join(workdir_abs, ".git")):
        return f"{action} erfordert ein Git-Repository als workdir{git_hint}."
    return None


def run_claude_write_armed(
    prompt: str,
    model: str | None = None,
    timeout: int | None = None,
    workdir: str | None = None,
    *,
    budget_error: tuple[int, str, str] | None,
    resolve_runtime_config: Callable[[], dict],
    settings: Any,
    provisioned_binary: Callable[[str], str | None],
    acquire_permit: Callable[..., AbstractContextManager[Any]],
    logger: logging.Logger,
) -> dict:
    """COMMON-001-Follow-up: schreibender Claude-Run im isolierten Workspace.

    Das Originalprojekt wird nie veraendert. Ablauf:

    1. ``workdir`` (muss ein Git-Repo innerhalb von
       ``claude_cli.allowed_paths`` sein) wird in ein Temp-Verzeichnis
       kopiert und dort mit einem Baseline-Commit eingefroren.
    2. Claude laeuft in der Kopie mit ``--permission-mode acceptEdits``
       (der einzige Kontext, in dem dieser Modus erlaubt ist).
    3. Die Aenderungen werden als ``git diff`` plus Dateiliste als
       Artefakt zurueckgegeben; der Temp-Workspace wird geloescht.

    Der Diff wird bewusst NICHT angewendet — das Ergebnis hat den Status
    ``awaiting_diff_review`` und der Nutzer entscheidet ueber die
    Uebernahme (Approval-Gate). Grosse Workspaces werden 1:1 kopiert;
    fuer Monorepos ist das der falsche Pfad.
    """
    result: dict = {
        "status": "error",
        "rc": -1,
        "stdout": "",
        "stderr": "",
        "diff": "",
        "diff_truncated": False,
        "changed_files": [],
        "write_armed": True,
    }

    if budget_error is not None:
        result["stderr"] = budget_error[2]
        return result

    runtime_cfg = resolve_runtime_config()
    gate_error = _allowed_git_workdir_error(workdir, runtime_cfg, action="write_armed", git_hint=" (Diff-Basis)")
    if gate_error is not None:
        result["stderr"] = gate_error
        return result
    workdir_abs = os.path.realpath(str(workdir))

    claude_bin = runtime_cfg["command"]
    claude_resolved = shutil.which(claude_bin) or provisioned_binary("claude_code")
    if claude_resolved is None:
        result["stderr"] = f"Claude binary '{claude_bin}' not found. Install with: npm i -g @anthropic-ai/claude-code"
        return result

    effective_timeout = int(timeout or runtime_cfg["timeout_seconds"])
    args = [claude_resolved, "-p", prompt, "--permission-mode", "acceptEdits", "--output-format", "text"]
    selected_model = str(model or runtime_cfg["default_model"] or "").strip()
    if selected_model and selected_model not in ("claude-code-default", "default"):
        args.extend(["--model", selected_model])

    tmp_root = tempfile.mkdtemp(prefix="ananta-claude-write-armed-")
    try:
        workspace = os.path.join(tmp_root, "workspace")
        shutil.copytree(workdir_abs, workspace, symlinks=True, ignore=ignore_transient_git_locks)
        rc, _, err = run_git(["add", "-A"], workspace)
        if rc == 0:
            rc, _, err = run_git(
                [*WRITE_ARMED_GIT_IDENTITY, "commit", "--allow-empty", "-m", "ananta write_armed baseline"],
                workspace,
            )
        if rc != 0:
            result["stderr"] = f"Baseline-Commit im isolierten Workspace fehlgeschlagen: {err.strip()}"
            return result

        with acquire_permit("claude_code", timeout=effective_timeout) as ticket:
            if not ticket.acquired:
                result["stderr"] = "Backend 'claude_code' ist ausgelastet (semaphore_exhausted)"
                return result
            env = os.environ.copy()
            if runtime_cfg["auth_mode"] == "claude_login":
                env.pop("ANTHROPIC_API_KEY", None)
            elif not env.get("ANTHROPIC_API_KEY") and getattr(settings, "anthropic_api_key", None):
                env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
            try:
                redacted_args = args[:1] + ["-p", "<prompt>"] + args[3:]
                logger.info(f"Claude write_armed Aufruf (isolierter Workspace): {redacted_args}")
                proc = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                    args,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=effective_timeout,
                    cwd=workspace,
                )
                result["rc"] = proc.returncode
                result["stdout"] = (proc.stdout or "")[:8000]
                result["stderr"] = (proc.stderr or "")[:8000]
            except subprocess.TimeoutExpired:
                result["stderr"] = "Timeout"
                return result

        rc, _, err = run_git(["add", "-A"], workspace)
        if rc != 0:
            result["stderr"] = (result["stderr"] + f"\nDiff-Erfassung fehlgeschlagen: {err.strip()}").strip()
            return result
        _, changed, _ = run_git(["diff", "--cached", "--name-only"], workspace)
        _, diff_text, _ = run_git(["-c", "core.quotepath=false", "diff", "--cached"], workspace)
        result["changed_files"] = [line.strip() for line in changed.splitlines() if line.strip()]
        if len(diff_text) > WRITE_ARMED_MAX_DIFF_CHARS:
            result["diff"] = diff_text[:WRITE_ARMED_MAX_DIFF_CHARS]
            result["diff_truncated"] = True
        else:
            result["diff"] = diff_text
        result["status"] = "awaiting_diff_review" if result["changed_files"] else "no_changes"
        return result
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def apply_reviewed_diff(
    diff: str,
    workdir: str | None = None,
    *,
    resolve_runtime_config: Callable[[], dict],
) -> dict:
    """Diff-Apply nach Review: wendet einen (vom Nutzer geprueften)
    write_armed-Diff auf das Original-Workdir an.

    Dieselben Gates wie run_claude_write_armed: claude_cli.enabled,
    workdir ist Git-Repo innerhalb von claude_cli.allowed_paths.
    Ablauf: erst ``git apply --check`` (Validierung, u.a. gegen
    abgeschnittene Diffs und Konflikte mit lokalem Stand), dann
    ``git apply`` in den Working Tree. Es wird bewusst NICHT
    committet — die Aenderungen bleiben im ``git status`` sichtbar
    und der Commit ist die letzte manuelle Review-Entscheidung.
    """
    result: dict = {
        "status": "error",
        "stderr": "",
        "changed_files": [],
        "applied": False,
    }
    diff_text = str(diff or "")
    if not diff_text.strip():
        result["stderr"] = "Leerer Diff — nichts anzuwenden."
        return result
    if len(diff_text) > WRITE_ARMED_MAX_DIFF_CHARS:
        result["stderr"] = (
            f"Diff ist groesser als {WRITE_ARMED_MAX_DIFF_CHARS} Zeichen — vermutlich abgeschnitten "
            "(diff_truncated). Abgeschnittene Diffs werden nicht angewendet."
        )
        return result

    runtime_cfg = resolve_runtime_config()
    gate_error = _allowed_git_workdir_error(workdir, runtime_cfg, action="Diff-Apply", git_hint="")
    if gate_error is not None:
        result["stderr"] = gate_error
        return result
    workdir_abs = os.path.realpath(str(workdir))

    rc, _, err = run_git(["apply", "--check", "--whitespace=nowarn", "-"], workdir_abs, input_text=diff_text)
    if rc != 0:
        result["status"] = "conflict"
        result["stderr"] = (
            "Diff laesst sich nicht sauber anwenden (lokaler Stand hat sich geaendert oder Diff ist "
            f"unvollstaendig): {err.strip()}"
        )
        return result

    _, numstat, _ = run_git(["apply", "--numstat", "-"], workdir_abs, input_text=diff_text)
    changed_files = [line.split("\t")[-1].strip() for line in numstat.splitlines() if line.strip()]

    rc, _, err = run_git(["apply", "--whitespace=nowarn", "-"], workdir_abs, input_text=diff_text)
    if rc != 0:
        result["stderr"] = f"git apply fehlgeschlagen: {err.strip()}"
        return result

    result["status"] = "applied"
    result["applied"] = True
    result["changed_files"] = changed_files
    return result
