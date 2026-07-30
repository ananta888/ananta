from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent.services.git_audit_service import record_git_activity
from agent.services.git_remote_policy_service import (
    GitRemoteAccessPolicyPort,
    GitRemotePolicyError,
    GitRemotePolicyRequest,
    GitTransportAuthorization,
    get_git_remote_access_policy,
    hardened_git_environment,
    hardened_git_transport_args,
)


class WorkspaceGitInitError(RuntimeError):
    def __init__(
        self,
        message: str,
        workspace_dir: Path,
        stderr: str = "",
        *,
        reason_code: str = "workspace_git_initialization_failed",
    ) -> None:
        super().__init__(message)
        self.workspace_dir = workspace_dir
        self.stderr = stderr
        self.reason_code = reason_code


@dataclass(frozen=True)
class WorkspaceGitContext:
    workspace_dir: Path
    repo_root: Path
    branch: str
    remote_url: Optional[str]
    is_clone: bool
    credential_ref: Optional[str] = None


def _sanitize_segment(value: str, max_len: int = 12) -> str:
    raw = re.sub(r"[^a-z0-9-]", "", str(value).lower())
    return raw[:max_len].strip("-") or "workspace"


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    for key in tuple(environment):
        if (
            key.startswith("GIT_CONFIG_")
            or key.startswith("GIT_SSL_")
            or key
            in {
                "GIT_ASKPASS",
                "SSH_ASKPASS",
                "GIT_PROXY_COMMAND",
                "GIT_SSH",
                "GIT_SSH_COMMAND",
            }
        ):
            environment.pop(key, None)
    environment.update(hardened_git_environment())
    try:
        return subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
    except Exception as exc:
        raise WorkspaceGitInitError(
            f"git {args[0]} failed: {exc}", workspace_dir=cwd
        ) from exc


def _audit_commit_and_push(
    workspace_dir: Path,
    *,
    branch: str,
    outcome: str,
    task_id: str | None = None,
    commit_sha: str = "",
) -> None:
    record_git_activity(
        "workspace_git_commit_push",
        workspace_dir=workspace_dir,
        operation="commit_push",
        outcome=outcome,
        branch=branch,
        task_id=task_id,
        commit_sha=commit_sha,
        summary=f"Ananta workspace commit/push: {outcome}",
    )


class WorkspaceGitService:
    def __init__(
        self,
        *,
        remote_policy: GitRemoteAccessPolicyPort | None = None,
    ) -> None:
        self._remote_policy = remote_policy or get_git_remote_access_policy()

    @staticmethod
    def init_bare_repo(bare_path: Path) -> None:
        """Create a bare git repo at bare_path if it does not already exist."""
        if bare_path.exists():
            return
        bare_path.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", "init", "--bare", str(bare_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            raise WorkspaceGitInitError(
                f"git init --bare failed: {res.stderr}",
                workspace_dir=bare_path,
                stderr=res.stderr,
            )
        logging.info("Bare git repo created at %s", bare_path)

    def commit_and_push(
        self,
        workspace_dir: Path,
        *,
        branch: str,
        message: str,
        task_id: str | None = None,
    ) -> bool:
        """Stage all workspace changes, commit, and push to remote.

        Returns True if changes were pushed, False if nothing to commit.
        Silently swallows errors so a push failure never aborts task reporting.
        """
        workspace_dir = Path(workspace_dir)
        try:
            staged = _run_git(["add", "-A"], cwd=workspace_dir)
            if staged.returncode != 0:
                _audit_commit_and_push(
                    workspace_dir,
                    branch=branch,
                    outcome="stage_failed",
                    task_id=task_id,
                )
                return False
            check = _run_git(["diff", "--cached", "--quiet"], cwd=workspace_dir)
            if check.returncode == 0:
                _audit_commit_and_push(
                    workspace_dir,
                    branch=branch,
                    outcome="no_changes",
                    task_id=task_id,
                )
                return False
            commit = _run_git(
                [
                    "-c", "user.name=ananta-worker",
                    "-c", "user.email=worker@ananta",
                    "commit", "-m", message,
                ],
                cwd=workspace_dir,
            )
            if commit.returncode != 0:
                _audit_commit_and_push(
                    workspace_dir,
                    branch=branch,
                    outcome="commit_failed",
                    task_id=task_id,
                )
                return False
            head = _run_git(["rev-parse", "--verify", "HEAD"], cwd=workspace_dir)
            commit_sha = head.stdout.strip() if head.returncode == 0 else ""
            remote = _run_git(["remote", "get-url", "origin"], cwd=workspace_dir)
            if remote.returncode != 0:
                _audit_commit_and_push(
                    workspace_dir,
                    branch=branch,
                    outcome="remote_url_unavailable",
                    task_id=task_id,
                    commit_sha=commit_sha,
                )
                return False
            try:
                policy_request = GitRemotePolicyRequest(
                    remote_url=remote.stdout.strip(),
                    operation="push",
                )
                authorized_remote = self._remote_policy.authorize(
                    policy_request
                )
                transport_authorization = (
                    GitTransportAuthorization.create(
                        authorized=authorized_remote,
                        request=policy_request,
                    )
                )
                transport_authorization.validate()
            except GitRemotePolicyError as exc:
                _audit_commit_and_push(
                    workspace_dir,
                    branch=branch,
                    outcome=exc.reason_code,
                    task_id=task_id,
                    commit_sha=commit_sha,
                )
                return False
            res = _run_git(
                hardened_git_transport_args(
                    transport_authorization,
                    ["push", "origin", f"HEAD:{branch}"],
                    remote_name="origin",
                ),
                cwd=workspace_dir,
            )
            if res.returncode != 0:
                logging.warning("git push failed for %s: %s", workspace_dir, res.stderr)
                _audit_commit_and_push(
                    workspace_dir,
                    branch=branch,
                    outcome="push_failed",
                    task_id=task_id,
                    commit_sha=commit_sha,
                )
                return False
            logging.info("git push ok: %s -> %s", workspace_dir, branch)
            _audit_commit_and_push(
                workspace_dir,
                branch=branch,
                outcome="pushed",
                task_id=task_id,
                commit_sha=commit_sha,
            )
            return True
        except Exception as exc:
            logging.warning("commit_and_push error for %s: %s", workspace_dir, exc)
            _audit_commit_and_push(
                workspace_dir,
                branch=branch,
                outcome="failed",
                task_id=task_id,
            )
            return False

    def init_workspace(
        self,
        workspace_dir: Path,
        *,
        remote_url: Optional[str],
        branch: str,
        credential_ref: Optional[str] = None,
        enabled: bool = True,
    ) -> WorkspaceGitContext:
        workspace_dir = Path(workspace_dir)
        authorized_remote = None
        transport_authorization = None
        if remote_url:
            try:
                policy_request = GitRemotePolicyRequest(
                    remote_url=remote_url,
                    operation="clone" if enabled else "configure",
                    credential_ref=credential_ref,
                    allow_redirects=False,
                    proxy_url=None,
                    recurse_submodules=False,
                    lfs_mode="pointer_only",
                )
                authorized_remote = self._remote_policy.authorize(
                    policy_request
                )
                transport_authorization = (
                    GitTransportAuthorization.create(
                        authorized=authorized_remote,
                        request=policy_request,
                    )
                )
                transport_authorization.validate()
            except GitRemotePolicyError as exc:
                raise WorkspaceGitInitError(
                    "Git remote policy denied workspace initialization",
                    workspace_dir=workspace_dir,
                    reason_code=exc.reason_code,
                ) from exc
        if not enabled:
            return WorkspaceGitContext(
                workspace_dir=workspace_dir,
                repo_root=workspace_dir,
                branch=branch,
                remote_url=authorized_remote.redacted_url if authorized_remote else None,
                is_clone=False,
                credential_ref=authorized_remote.credential_ref if authorized_remote else None,
            )

        git_dir = workspace_dir / ".git"
        is_clone = bool(remote_url)

        if git_dir.exists():
            actual_remote = _run_git(["remote", "get-url", "origin"], cwd=workspace_dir)
            if actual_remote.returncode == 0:
                try:
                    actual_authorization = self._remote_policy.authorize(
                        GitRemotePolicyRequest(
                            remote_url=actual_remote.stdout.strip(),
                            operation="configure",
                            credential_ref=credential_ref,
                        )
                    )
                except GitRemotePolicyError as exc:
                    raise WorkspaceGitInitError(
                        "Existing Git remote is denied by policy",
                        workspace_dir=workspace_dir,
                        reason_code=exc.reason_code,
                    ) from exc
                if (
                    authorized_remote is not None
                    and actual_authorization.canonical_url
                    != authorized_remote.canonical_url
                ):
                    raise WorkspaceGitInitError(
                        "Existing Git remote does not match configured remote",
                        workspace_dir=workspace_dir,
                        reason_code="git_remote_binding_mismatch",
                    )
                authorized_remote = actual_authorization
            self._ensure_branch(workspace_dir, branch=branch)
            return WorkspaceGitContext(
                workspace_dir=workspace_dir,
                repo_root=workspace_dir,
                branch=branch,
                remote_url=authorized_remote.redacted_url if authorized_remote else None,
                is_clone=is_clone,
                credential_ref=authorized_remote.credential_ref if authorized_remote else None,
            )

        workspace_dir.mkdir(parents=True, exist_ok=True)

        if remote_url:
            assert authorized_remote is not None
            assert transport_authorization is not None
            res = _run_git(
                hardened_git_transport_args(
                    transport_authorization,
                    [
                        "clone",
                        "--no-local",
                        "--no-recurse-submodules",
                        authorized_remote.canonical_url,
                        str(workspace_dir),
                    ]
                ),
                cwd=workspace_dir.parent,
            )
            if res.returncode != 0:
                raise WorkspaceGitInitError(
                    "git clone failed for authorized remote",
                    workspace_dir=workspace_dir,
                    stderr=res.stderr,
                    reason_code="workspace_git_clone_failed",
                )
            self._ensure_branch(workspace_dir, branch=branch)
            self._write_gitignore(workspace_dir)
        else:
            res = _run_git(["init"], cwd=workspace_dir)
            if res.returncode != 0:
                raise WorkspaceGitInitError(
                    "git init failed",
                    workspace_dir=workspace_dir,
                    stderr=res.stderr,
                )
            res = _run_git(["checkout", "-b", branch], cwd=workspace_dir)
            if res.returncode != 0:
                res2 = _run_git(["checkout", branch], cwd=workspace_dir)
                if res2.returncode != 0:
                    raise WorkspaceGitInitError(
                        f"Failed to create/checkout branch '{branch}'",
                        workspace_dir=workspace_dir,
                        stderr=res2.stderr,
                    )

        return WorkspaceGitContext(
            workspace_dir=workspace_dir,
            repo_root=workspace_dir,
            branch=branch,
            remote_url=authorized_remote.redacted_url if authorized_remote else None,
            is_clone=is_clone,
            credential_ref=authorized_remote.credential_ref if authorized_remote else None,
        )

    @staticmethod
    def _write_gitignore(workspace_dir: Path) -> None:
        """Write a .gitignore if one doesn't already exist."""
        gi = workspace_dir / ".gitignore"
        if gi.exists():
            return
        gi.write_text(
            "__pycache__/\n*.pyc\n*.pyo\n.ananta/\nartifacts/\nrag_helper/\n",
            encoding="utf-8",
        )

    def _ensure_branch(self, workspace_dir: Path, *, branch: str) -> None:
        res = _run_git(["checkout", branch], cwd=workspace_dir)
        if res.returncode != 0:
            # Try tracking remote branch (exists in origin but not locally yet)
            res_track = _run_git(["checkout", "-b", branch, f"origin/{branch}"], cwd=workspace_dir)
            if res_track.returncode != 0:
                # Remote branch doesn't exist — create local branch from current HEAD (or orphan)
                res2 = _run_git(["checkout", "-b", branch], cwd=workspace_dir)
                if res2.returncode != 0:
                    raise WorkspaceGitInitError(
                        f"Failed to checkout branch '{branch}'",
                        workspace_dir=workspace_dir,
                        stderr=res2.stderr,
                    )

    def resolve_branch_name(
        self,
        goal_id: str,
        worker_key: Optional[str],
        strategy: str,
    ) -> str:
        safe_goal = _sanitize_segment(str(goal_id or ""), max_len=12)
        if strategy == "goal_worker" and worker_key:
            safe_worker = _sanitize_segment(str(worker_key), max_len=20)
            name = f"goal/{safe_goal}/{safe_worker}"
        else:
            name = f"goal/{safe_goal}"
        return name[:80]


_instance: Optional[WorkspaceGitService] = None


def get_workspace_git_service() -> WorkspaceGitService:
    global _instance
    if _instance is None:
        _instance = WorkspaceGitService()
    return _instance
