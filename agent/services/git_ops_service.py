from __future__ import annotations

from pathlib import Path
from typing import Iterable

from agent.common.audit import log_audit
from agent.services.commit_message_validator import CommitMessageValidator
from agent.services.ops_command_runner import CommandRunner, get_default_command_runner
from agent.services.ops_models import (
    GitChangedFile,
    GitCommitSummary,
    GitDiff,
    GitStatus,
    OpsActionResult,
    OpsError,
)
from agent.services.ops_policy_service import OpsPolicyService, get_ops_policy_service
from agent.services.ops_registry_service import OpsRegistryService, get_ops_registry_service


class GitOpsService:
    def __init__(
        self,
        *,
        registry: OpsRegistryService | None = None,
        runner: CommandRunner | None = None,
        policy: OpsPolicyService | None = None,
    ) -> None:
        self._registry = registry or get_ops_registry_service()
        self._runner = runner or get_default_command_runner()
        self._policy = policy or get_ops_policy_service()

    def status(self, workspace_id: str | None = None) -> GitStatus:
        workspace = self._registry.resolve_workspace(workspace_id)
        if workspace is None:
            return GitStatus(workspace_id=str(workspace_id or ""), error=OpsError("workspace_not_allowed", "workspace not registered"))
        if not self._runner.exists("git"):
            return GitStatus(workspace_id=workspace.workspace_id, error=OpsError("git_not_found", "git binary not found"))
        inside = self._git(["rev-parse", "--is-inside-work-tree"], cwd=workspace.root)
        if inside.timed_out:
            return GitStatus(workspace_id=workspace.workspace_id, error=OpsError("git_timeout", "git status timed out"))
        if inside.returncode != 0:
            return GitStatus(workspace_id=workspace.workspace_id, error=OpsError("git_not_repository", "workspace is not a git repository"))
        branch = self._git(["branch", "--show-current"], cwd=workspace.root).stdout.strip()
        upstream = self._git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=workspace.root)
        remote_name = ""
        upstream_name = ""
        if upstream.returncode == 0:
            upstream_name = upstream.stdout.strip()
            remote_name = upstream_name.split("/", 1)[0] if "/" in upstream_name else ""
        changed_files = self._changed_files(workspace.root)
        commits = self._recent_commits(workspace.root)
        return GitStatus(
            workspace_id=workspace.workspace_id,
            branch=branch,
            upstream=upstream_name,
            remote_name=remote_name,
            dirty=bool(changed_files),
            changed_files=changed_files,
            recent_commits=commits,
        )

    def diff(self, workspace_id: str | None = None, *, path: str | None = None, cached: bool = False) -> GitDiff:
        workspace = self._registry.resolve_workspace(workspace_id)
        if workspace is None:
            return GitDiff(workspace_id=str(workspace_id or ""), error=OpsError("workspace_not_allowed", "workspace not registered"))
        if path:
            resolved = self._registry.resolve_relative_path(workspace.workspace_id, path)
            if resolved is None:
                return GitDiff(
                    workspace_id=workspace.workspace_id,
                    path=str(path),
                    cached=cached,
                    error=OpsError("path_not_allowed", "path escapes workspace"),
                )
        args = ["diff", "--no-ext-diff"]
        if cached:
            args.append("--cached")
        if path:
            args.extend(["--", str(path)])
        result = self._git(args, cwd=workspace.root, timeout_seconds=10)
        if result.timed_out:
            return GitDiff(workspace_id=workspace.workspace_id, cached=cached, path=str(path or ""), error=OpsError("git_timeout", "git diff timed out"))
        if result.returncode != 0:
            return GitDiff(
                workspace_id=workspace.workspace_id,
                cached=cached,
                path=str(path or ""),
                error=OpsError("git_command_failed", result.stderr[:240]),
            )
        return GitDiff(workspace_id=workspace.workspace_id, cached=cached, path=str(path or ""), diff=result.stdout, truncated=result.truncated)

    def stage(self, workspace_id: str | None, paths: Iterable[str], *, staged: bool = True) -> OpsActionResult:
        action = "stage" if staged else "unstage"
        path_list = [str(path or "").strip() for path in paths if str(path or "").strip()]
        workspace = self._registry.resolve_workspace(workspace_id)
        if workspace is None:
            return OpsActionResult(False, action, error=OpsError("workspace_not_allowed", "workspace not registered"))
        if not path_list:
            return OpsActionResult(False, action, target_id=workspace.workspace_id, error=OpsError("path_not_allowed", "explicit paths required"))
        for rel in path_list:
            if self._registry.resolve_relative_path(workspace.workspace_id, rel) is None:
                return OpsActionResult(False, action, target_id=workspace.workspace_id, error=OpsError("path_not_allowed", "path escapes workspace"))
        decision = self._policy.evaluate(f"git.{action}", action, target_id=workspace.workspace_id)
        if not decision.allowed:
            return self._blocked_result(action, workspace.workspace_id, decision.decision, decision.reason_code, f"git.{action}", {"paths": path_list})
        args = ["add", "--"] if staged else ["restore", "--staged", "--"]
        result = self._git([*args, *path_list], cwd=workspace.root)
        ok = result.returncode == 0
        log_audit("ops_git_stage" if staged else "ops_git_unstage", {"workspace_id": workspace.workspace_id, "paths": path_list, "ok": ok})
        return OpsActionResult(ok, action, target_id=workspace.workspace_id, error=None if ok else OpsError("git_command_failed", result.stderr[:240]))

    def commit(self, workspace_id: str | None, message: str) -> OpsActionResult:
        workspace = self._registry.resolve_workspace(workspace_id)
        if workspace is None:
            return OpsActionResult(False, "commit", error=OpsError("workspace_not_allowed", "workspace not registered"))
        validation = CommitMessageValidator().validate(str(message or ""))
        if not validation.valid:
            return OpsActionResult(False, "commit", target_id=workspace.workspace_id, error=OpsError("invalid_commit_message", "invalid commit message", {"errors": validation.errors}))
        decision = self._policy.evaluate("git.commit", "commit", target_id=workspace.workspace_id)
        if not decision.allowed:
            return self._blocked_result("commit", workspace.workspace_id, decision.decision, decision.reason_code, "git.commit", {"message": message})
        result = self._git(["commit", "-m", message], cwd=workspace.root)
        ok = result.returncode == 0
        log_audit("ops_git_commit", {"workspace_id": workspace.workspace_id, "ok": ok})
        return OpsActionResult(ok, "commit", target_id=workspace.workspace_id, error=None if ok else OpsError("git_command_failed", result.stderr[:240]))

    def push(self, workspace_id: str | None) -> OpsActionResult:
        workspace = self._registry.resolve_workspace(workspace_id)
        if workspace is None:
            return OpsActionResult(False, "push", error=OpsError("workspace_not_allowed", "workspace not registered"))
        decision = self._policy.evaluate("git.push", "push", target_id=workspace.workspace_id)
        if not decision.allowed:
            return self._blocked_result("push", workspace.workspace_id, decision.decision, decision.reason_code, "git.push", {})
        result = self._git(["push"], cwd=workspace.root, timeout_seconds=30)
        ok = result.returncode == 0
        log_audit("ops_git_push", {"workspace_id": workspace.workspace_id, "ok": ok, "returncode": result.returncode})
        return OpsActionResult(ok, "push", target_id=workspace.workspace_id, error=None if ok else OpsError("git_command_failed", result.stderr[:240]))

    def _changed_files(self, cwd: Path) -> list[GitChangedFile]:
        result = self._git(["status", "--porcelain=v1"], cwd=cwd)
        files: list[GitChangedFile] = []
        if result.returncode != 0:
            return files
        for line in result.stdout.splitlines():
            if not line:
                continue
            index_status = line[0:1].strip()
            worktree_status = line[1:2].strip()
            rel = line[3:].strip()
            if " -> " in rel:
                rel = rel.split(" -> ", 1)[1]
            files.append(
                GitChangedFile(
                    path=rel,
                    index_status=index_status,
                    worktree_status=worktree_status,
                    staged=bool(index_status and index_status != "?"),
                    unstaged=bool(worktree_status and worktree_status != "?"),
                    untracked=index_status == "?" or worktree_status == "?",
                )
            )
        return files

    def _recent_commits(self, cwd: Path) -> list[GitCommitSummary]:
        result = self._git(["log", "-n", "5", "--pretty=format:%h%x09%s"], cwd=cwd)
        commits: list[GitCommitSummary] = []
        if result.returncode != 0:
            return commits
        for line in result.stdout.splitlines():
            sha, _, subject = line.partition("\t")
            if sha:
                commits.append(GitCommitSummary(sha=sha, subject=subject))
        return commits

    def _git(self, args: list[str], *, cwd: Path, timeout_seconds: int | None = None):
        return self._runner.run(["git", *args], cwd=cwd, timeout_seconds=timeout_seconds)

    def _blocked_result(self, action: str, target_id: str, decision: str, reason_code: str, tool_name: str, arguments: dict) -> OpsActionResult:
        code = "approval_required" if decision == "approval_required" else "policy_denied"
        approval_id = None
        if decision == "approval_required":
            approval_id = self._policy.create_approval_request(
                tool_name=tool_name,
                action=action,
                target_id=target_id,
                arguments={"workspace_id": target_id, **arguments},
            )
        log_audit("ops_git_mutation_blocked", {"action": action, "target_id": target_id, "decision": decision, "reason_code": reason_code, "approval_id": approval_id})
        return OpsActionResult(False, action, target_id=target_id, decision=decision, approval_id=approval_id, error=OpsError(code, reason_code))


_default_git_ops_service: GitOpsService | None = None


def get_git_ops_service() -> GitOpsService:
    global _default_git_ops_service
    if _default_git_ops_service is None:
        _default_git_ops_service = GitOpsService()
    return _default_git_ops_service
