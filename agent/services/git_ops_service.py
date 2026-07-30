from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from agent.common.audit import log_audit
from agent.services.commit_message_validator import CommitMessageValidator
from agent.services.git_audit_service import git_workspace_fingerprint
from agent.services.git_remote_policy_service import (
    GitRemoteAccessPolicyPort,
    GitRemotePolicyError,
    GitRemotePolicyRequest,
    GitTransportAuthorization,
    get_git_remote_access_policy,
    hardened_git_environment,
    hardened_git_transport_args,
)
from agent.services.ops_command_runner import CommandResult, CommandRunner, get_default_command_runner
from agent.services.ops_models import (
    GitActivity,
    GitActivityEvent,
    GitBranch,
    GitBranches,
    GitChangedFile,
    GitChanges,
    GitCommitSummary,
    GitDiff,
    GitDiffStat,
    GitHistory,
    GitRemote,
    GitRemotes,
    GitStatus,
    OpsActionResult,
    OpsError,
)
from agent.services.ops_policy_service import OpsPolicyService, get_ops_policy_service
from agent.services.ops_registry_service import OpsRegistryService, WorkspaceRef, get_ops_registry_service

_CONFLICT_STATES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
_SAFE_REMOTE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,239}$")


class GitOpsService:
    """Hub-side, workspace-scoped Git control surface.

    Read models expose repository state and reflog/audit provenance. Mutations
    accept explicit paths or the current registered upstream only; they never
    execute arbitrary refs, paths, remotes or shell fragments supplied by a
    client.
    """

    def __init__(
        self,
        *,
        registry: OpsRegistryService | None = None,
        runner: CommandRunner | None = None,
        policy: OpsPolicyService | None = None,
        remote_policy: GitRemoteAccessPolicyPort | None = None,
    ) -> None:
        self._registry = registry or get_ops_registry_service()
        self._runner = runner or get_default_command_runner()
        self._policy = policy or get_ops_policy_service()
        self._remote_policy = remote_policy or get_git_remote_access_policy()

    # ------------------------------------------------------------------ reads

    def workspaces(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for ref in self._registry.workspaces():
            repository = self._is_repository(ref.root)
            items.append(
                {
                    "workspace_id": ref.workspace_id,
                    "label": ref.label or ("Ananta Repository" if ref.workspace_id == "repo" else ref.workspace_id),
                    "is_default": ref.workspace_id == "repo",
                    "repository": repository,
                    "source": ref.source,
                }
            )
        return items

    def status(self, workspace_id: str | None = None) -> GitStatus:
        workspace, error = self._workspace(workspace_id)
        if error:
            return GitStatus(workspace_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = self._validate_repository(workspace)
        if validation:
            return GitStatus(workspace_id=workspace.workspace_id, error=validation)

        branch_result = self._git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=workspace.root)
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
        head = self._git(["rev-parse", "--verify", "HEAD"], cwd=workspace.root)
        head_sha = head.stdout.strip() if head.returncode == 0 else ""
        upstream_result = self._git(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=workspace.root,
        )
        upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else ""
        configured_remotes = {item.name for item in self.remotes(workspace.workspace_id).items}
        remote_name = next(
            (name for name in sorted(configured_remotes, key=len, reverse=True) if upstream.startswith(f"{name}/")),
            "",
        )
        ahead, behind = self._ahead_behind(workspace.root, upstream)
        changed_files, truncated = self._changed_files(workspace.root)
        staged_count = sum(1 for item in changed_files if item.staged)
        unstaged_count = sum(1 for item in changed_files if item.unstaged)
        untracked_count = sum(1 for item in changed_files if item.untracked)
        conflict_count = sum(1 for item in changed_files if item.conflicted)
        operation_state = self._operation_state(workspace.root)
        return GitStatus(
            workspace_id=workspace.workspace_id,
            branch=branch,
            head_sha=head_sha,
            upstream=upstream,
            remote_name=remote_name,
            detached=bool(head_sha) and not bool(branch),
            ahead=ahead,
            behind=behind,
            operation_state=operation_state,
            dirty=bool(changed_files),
            conflict_count=conflict_count,
            staged_count=staged_count,
            unstaged_count=unstaged_count,
            untracked_count=untracked_count,
            can_commit=staged_count > 0 and conflict_count == 0 and operation_state == "idle",
            can_pull=bool(upstream) and not changed_files and operation_state == "idle",
            can_push=bool(branch and head_sha and configured_remotes)
            and conflict_count == 0
            and operation_state == "idle",
            truncated=truncated,
            changed_files=changed_files,
            recent_commits=self.history(workspace.workspace_id, limit=5).items,
        )

    def changes(self, workspace_id: str | None = None) -> GitChanges:
        status = self.status(workspace_id)
        if status.error:
            return GitChanges(workspace_id=status.workspace_id, error=status.error)
        return GitChanges(
            workspace_id=status.workspace_id,
            items=status.changed_files,
            count=len(status.changed_files),
            staged_count=status.staged_count,
            unstaged_count=status.unstaged_count,
            untracked_count=status.untracked_count,
            conflict_count=status.conflict_count,
            truncated=status.truncated,
        )

    def diff(
        self,
        workspace_id: str | None = None,
        *,
        path: str | None = None,
        cached: bool = False,
        scope: str | None = None,
    ) -> GitDiff:
        workspace, error = self._workspace(workspace_id)
        selected_scope = str(scope or ("staged" if cached else "unstaged")).strip().lower()
        if selected_scope not in {"staged", "unstaged", "combined"}:
            return GitDiff(
                workspace_id=str(workspace_id or ""),
                cached=cached,
                path=str(path or ""),
                scope=selected_scope,
                error=OpsError("git_command_failed", "scope must be staged, unstaged or combined"),
            )
        if error:
            return GitDiff(
                workspace_id=str(workspace_id or ""),
                cached=cached,
                path=str(path or ""),
                scope=selected_scope,
                error=error,
            )
        assert workspace is not None
        validation = self._validate_repository(workspace)
        if validation:
            return GitDiff(
                workspace_id=workspace.workspace_id,
                cached=cached,
                path=str(path or ""),
                scope=selected_scope,
                error=validation,
            )
        normalized_path, path_error = self._relative_path(workspace, path) if path else ("", None)
        if path_error:
            return GitDiff(
                workspace_id=workspace.workspace_id,
                cached=cached,
                path=str(path or ""),
                scope=selected_scope,
                error=path_error,
            )

        staged = self._diff_command(workspace.root, "staged", normalized_path)
        unstaged = self._diff_command(workspace.root, "unstaged", normalized_path)
        combined = self._diff_command(workspace.root, "combined", normalized_path)
        for result in (staged, unstaged, combined):
            if result.timed_out:
                return GitDiff(
                    workspace_id=workspace.workspace_id,
                    cached=cached,
                    path=normalized_path,
                    scope=selected_scope,
                    error=OpsError("git_timeout", "git diff timed out"),
                )
            if result.returncode != 0:
                return GitDiff(
                    workspace_id=workspace.workspace_id,
                    cached=cached,
                    path=normalized_path,
                    scope=selected_scope,
                    error=OpsError("git_command_failed", result.stderr[:240]),
                )

        untracked_diff, untracked_truncated = self._untracked_diff(workspace.root, normalized_path)
        unstaged_text = unstaged.stdout + untracked_diff
        combined_text = combined.stdout + untracked_diff
        selected = {"staged": staged.stdout, "unstaged": unstaged_text, "combined": combined_text}[selected_scope]
        stats = self._diff_stats(workspace.root, selected_scope, normalized_path)
        if selected_scope in {"unstaged", "combined"}:
            stats.extend(self._untracked_stats(workspace.root, normalized_path))
        additions = sum(item.additions for item in stats)
        deletions = sum(item.deletions for item in stats)
        head = self._git(["rev-parse", "--verify", "HEAD"], cwd=workspace.root)
        return GitDiff(
            workspace_id=workspace.workspace_id,
            cached=selected_scope == "staged",
            path=normalized_path,
            scope=selected_scope,
            head_sha=head.stdout.strip() if head.returncode == 0 else "",
            diff=selected,
            staged_diff=staged.stdout,
            unstaged_diff=unstaged.stdout,
            untracked_diff=untracked_diff,
            stats=stats,
            additions=additions,
            deletions=deletions,
            files_changed=len(stats),
            truncated=staged.truncated or unstaged.truncated or combined.truncated or untracked_truncated,
        )

    def history(
        self,
        workspace_id: str | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
        path: str | None = None,
    ) -> GitHistory:
        workspace, error = self._workspace(workspace_id)
        safe_limit = self._bounded_int(limit, default=50, minimum=1, maximum=200)
        safe_offset = self._bounded_int(offset, default=0, minimum=0, maximum=100_000)
        if error:
            return GitHistory(workspace_id=str(workspace_id or ""), limit=safe_limit, offset=safe_offset, error=error)
        assert workspace is not None
        validation = self._validate_repository(workspace)
        if validation:
            return GitHistory(
                workspace_id=workspace.workspace_id, limit=safe_limit, offset=safe_offset, error=validation
            )
        normalized_path, path_error = self._relative_path(workspace, path) if path else ("", None)
        if path_error:
            return GitHistory(
                workspace_id=workspace.workspace_id, limit=safe_limit, offset=safe_offset, error=path_error
            )
        separator = "\x1f"
        args = [
            "log",
            "--all",
            f"--max-count={safe_limit + 1}",
            f"--skip={safe_offset}",
            "--date=iso-strict",
            f"--pretty=format:%H{separator}%h{separator}%s{separator}%an{separator}%ae{separator}%aI{separator}%P{separator}%D",
        ]
        if normalized_path:
            args.extend(["--", self._literal_pathspec(normalized_path)])
        result = self._git(
            args,
            cwd=workspace.root,
        )
        if result.returncode != 0:
            # An empty, unborn repository has no history but is not an API error.
            if (
                "does not have any commits" in result.stderr
                or "unknown revision" in result.stderr
                or "bad default revision" in result.stderr
            ):
                return GitHistory(workspace_id=workspace.workspace_id, limit=safe_limit, offset=safe_offset)
            return GitHistory(
                workspace_id=workspace.workspace_id,
                limit=safe_limit,
                offset=safe_offset,
                error=OpsError("git_command_failed", result.stderr[:240]),
            )
        items = [self._commit_from_line(line, separator) for line in result.stdout.splitlines() if line.strip()]
        items = [item for item in items if item is not None]
        has_more = len(items) > safe_limit
        items = items[:safe_limit]
        return GitHistory(
            workspace_id=workspace.workspace_id,
            items=items,
            count=len(items),
            limit=safe_limit,
            offset=safe_offset,
            has_more=has_more,
        )

    def branches(self, workspace_id: str | None = None) -> GitBranches:
        workspace, error = self._workspace(workspace_id)
        if error:
            return GitBranches(workspace_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = self._validate_repository(workspace)
        if validation:
            return GitBranches(workspace_id=workspace.workspace_id, error=validation)
        separator = "\x1f"
        result = self._git(
            [
                "for-each-ref",
                "--sort=-committerdate",
                f"--format=%(refname:short){separator}%(HEAD){separator}%(upstream:short){separator}%(objectname){separator}%(subject){separator}%(committerdate:iso-strict)",
                "refs/heads",
                "refs/remotes",
            ],
            cwd=workspace.root,
        )
        if result.returncode != 0:
            return GitBranches(
                workspace_id=workspace.workspace_id, error=OpsError("git_command_failed", result.stderr[:240])
            )
        remotes = {item.name for item in self.remotes(workspace.workspace_id).items}
        items: list[GitBranch] = []
        for line in result.stdout.splitlines():
            parts = line.split(separator)
            if len(parts) < 6:
                continue
            name, head_marker, upstream, sha, subject, committed_at = parts[:6]
            is_remote = any(name.startswith(f"{remote}/") for remote in remotes)
            ahead, behind = (0, 0) if is_remote else self._ahead_behind(workspace.root, upstream)
            items.append(
                GitBranch(
                    name=name,
                    current=head_marker.strip() == "*",
                    remote=is_remote,
                    upstream=upstream,
                    ahead=ahead,
                    behind=behind,
                    sha=sha,
                    last_commit_sha=sha,
                    last_commit_subject=subject,
                    last_commit_at=committed_at,
                )
            )
        return GitBranches(workspace_id=workspace.workspace_id, items=items, count=len(items))

    def remotes(self, workspace_id: str | None = None) -> GitRemotes:
        workspace, error = self._workspace(workspace_id)
        if error:
            return GitRemotes(workspace_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = self._validate_repository(workspace)
        if validation:
            return GitRemotes(workspace_id=workspace.workspace_id, error=validation)
        result = self._git(["remote", "-v"], cwd=workspace.root)
        if result.returncode != 0:
            return GitRemotes(
                workspace_id=workspace.workspace_id, error=OpsError("git_command_failed", result.stderr[:240])
            )
        values: dict[str, dict[str, str]] = {}
        for line in result.stdout.splitlines():
            match = re.match(r"^(\S+)\s+(.+?)\s+\((fetch|push)\)$", line.strip())
            if not match:
                continue
            name, url, kind = match.groups()
            values.setdefault(name, {})[kind] = self._redact_remote_url(url)
        items = [
            GitRemote(name=name, fetch_url=value.get("fetch", ""), push_url=value.get("push", ""))
            for name, value in sorted(values.items())
        ]
        return GitRemotes(workspace_id=workspace.workspace_id, items=items, count=len(items))

    def activity(self, workspace_id: str | None = None, *, limit: int = 100) -> GitActivity:
        workspace, error = self._workspace(workspace_id)
        safe_limit = self._bounded_int(limit, default=100, minimum=1, maximum=300)
        if error:
            return GitActivity(workspace_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = self._validate_repository(workspace)
        if validation:
            return GitActivity(workspace_id=workspace.workspace_id, error=validation)
        events = self._reflog_activity(workspace, safe_limit)
        events.extend(self._audit_activity(workspace, safe_limit))
        events.sort(key=lambda item: item.timestamp, reverse=True)
        deduplicated: list[GitActivityEvent] = []
        seen: set[tuple[str, str, str, str]] = set()
        for event in events:
            key = (event.source, event.timestamp, event.id, event.summary)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(event)
        return GitActivity(
            workspace_id=workspace.workspace_id,
            items=deduplicated[:safe_limit],
            count=min(len(deduplicated), safe_limit),
        )

    # --------------------------------------------------------------- mutations

    def stage(
        self,
        workspace_id: str | None,
        paths: Iterable[str],
        *,
        staged: bool = True,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        if not staged:
            return self.unstage(workspace_id, paths, approval_id=approval_id)
        return self._path_mutation(
            workspace_id,
            paths,
            action="stage",
            tool_name="git.stage",
            command=lambda selected: ["add", "--", *[self._literal_pathspec(path) for path in selected]],
            approval_id=approval_id,
        )

    def unstage(
        self, workspace_id: str | None, paths: Iterable[str], *, approval_id: str | None = None
    ) -> OpsActionResult:
        return self._path_mutation(
            workspace_id,
            paths,
            action="unstage",
            tool_name="git.unstage",
            command=lambda selected: [
                "restore",
                "--staged",
                "--",
                *[self._literal_pathspec(path) for path in selected],
            ],
            approval_id=approval_id,
        )

    def discard(
        self, workspace_id: str | None, paths: Iterable[str], *, approval_id: str | None = None
    ) -> OpsActionResult:
        workspace, selected, error = self._mutation_paths(workspace_id, paths)
        if error:
            return OpsActionResult(False, "discard", target_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        status = self.status(workspace.workspace_id)
        if status.error:
            return OpsActionResult(False, "discard", target_id=workspace.workspace_id, error=status.error)
        state_by_path = {item.path: item for item in status.changed_files}
        selected_states = [state_by_path.get(path) for path in selected]
        if any(item is None for item in selected_states):
            return self._failure(
                "discard",
                workspace.workspace_id,
                "git_path_state_invalid",
                "each path must be present in the current Git changes",
                paths=selected,
            )
        if any(item.untracked for item in selected_states):
            return self._failure(
                "discard",
                workspace.workspace_id,
                "git_untracked_discard_denied",
                "untracked files are never deleted by Git Ops",
                paths=selected,
            )
        if any(not item.unstaged for item in selected_states):
            return self._failure(
                "discard",
                workspace.workspace_id,
                "git_path_state_invalid",
                "discard requires tracked unstaged changes",
                paths=selected,
            )
        if any(item.conflicted for item in selected_states):
            return self._failure(
                "discard",
                workspace.workspace_id,
                "git_conflict",
                "conflicted paths must be resolved explicitly",
                paths=selected,
            )
        if any(item.renamed for item in selected_states):
            return self._failure(
                "discard",
                workspace.workspace_id,
                "git_path_state_invalid",
                "renames must be unstaged or resolved explicitly",
                paths=selected,
            )
        arguments = {"workspace_id": workspace.workspace_id, "paths": selected}
        blocked = self._authorize("git.discard", "discard", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        result = self._git(
            ["restore", "--worktree", "--", *[self._literal_pathspec(path) for path in selected]],
            cwd=workspace.root,
        )
        return self._command_result("discard", "git.discard", workspace.workspace_id, arguments, result, approval_id)

    def commit(self, workspace_id: str | None, message: str, *, approval_id: str | None = None) -> OpsActionResult:
        workspace, error = self._workspace(workspace_id)
        if error:
            return OpsActionResult(False, "commit", target_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = CommitMessageValidator().validate(str(message or ""))
        if not validation.valid:
            return self._failure(
                "commit",
                workspace.workspace_id,
                "invalid_commit_message",
                "invalid commit message",
                errors=validation.errors,
            )
        status = self.status(workspace.workspace_id)
        if status.error:
            return OpsActionResult(False, "commit", target_id=workspace.workspace_id, error=status.error)
        if status.conflict_count:
            return self._failure(
                "commit", workspace.workspace_id, "git_conflict", "conflicts must be resolved before commit"
            )
        if status.operation_state != "idle":
            return self._failure(
                "commit",
                workspace.workspace_id,
                "git_operation_in_progress",
                "finish the active Git operation before commit",
            )
        if not status.staged_count:
            return self._failure(
                "commit", workspace.workspace_id, "git_nothing_to_commit", "no staged changes to commit"
            )
        arguments = {"workspace_id": workspace.workspace_id, "message": str(message)}
        blocked = self._authorize("git.commit", "commit", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        result = self._git(["commit", "-m", str(message)], cwd=workspace.root, timeout_seconds=30)
        return self._command_result("commit", "git.commit", workspace.workspace_id, arguments, result, approval_id)

    def fetch(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        credential_ref: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        workspace, remote_name, error = self._fetch_target(
            workspace_id,
            remote=remote,
            credential_ref=credential_ref,
        )
        if error:
            return OpsActionResult(False, "fetch", target_id=str(workspace_id or ""), error=error)
        assert workspace is not None and remote_name is not None
        arguments = {
            "workspace_id": workspace.workspace_id,
            "remote": remote_name,
            "credential_ref": credential_ref,
        }
        blocked = self._authorize("git.fetch", "fetch", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        transport, transport_error = self._remote_transport_authorization(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation="fetch",
        )
        if transport_error:
            return OpsActionResult(
                False,
                "fetch",
                target_id=workspace.workspace_id,
                error=transport_error,
            )
        assert transport is not None
        result = self._git(
            hardened_git_transport_args(
                transport,
                ["fetch", "--no-tags", "--no-recurse-submodules", remote_name],
                remote_name=remote_name,
            ),
            cwd=workspace.root,
            timeout_seconds=60,
        )
        return self._command_result("fetch", "git.fetch", workspace.workspace_id, arguments, result, approval_id)

    def pull(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        branch: str | None = None,
        credential_ref: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        workspace, target, error = self._sync_target(
            workspace_id,
            remote=remote,
            branch=branch,
            credential_ref=credential_ref,
            operation="pull",
        )
        if error:
            return OpsActionResult(False, "pull", target_id=str(workspace_id or ""), error=error)
        assert workspace is not None and target is not None
        current = self.status(workspace.workspace_id)
        if current.dirty:
            return self._failure("pull", workspace.workspace_id, "git_dirty_worktree", "pull requires a clean worktree")
        if current.operation_state != "idle":
            return self._failure(
                "pull",
                workspace.workspace_id,
                "git_operation_in_progress",
                "finish the active Git operation before pull",
            )
        remote_name, branch_name = target
        arguments = {
            "workspace_id": workspace.workspace_id,
            "remote": remote_name,
            "branch": branch_name,
            "credential_ref": credential_ref,
        }
        blocked = self._authorize("git.pull", "pull_ff_only", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        transport, transport_error = self._remote_transport_authorization(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation="pull",
        )
        if transport_error:
            return OpsActionResult(
                False,
                "pull",
                target_id=workspace.workspace_id,
                error=transport_error,
            )
        assert transport is not None
        result = self._git(
            hardened_git_transport_args(
                transport,
                [
                    "pull",
                    "--ff-only",
                    "--no-rebase",
                    "--no-recurse-submodules",
                    remote_name,
                    branch_name,
                ],
                remote_name=remote_name,
            ),
            cwd=workspace.root,
            timeout_seconds=90,
        )
        return self._command_result("pull", "git.pull", workspace.workspace_id, arguments, result, approval_id)

    def push(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        branch: str | None = None,
        credential_ref: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        workspace, target, error = self._sync_target(
            workspace_id,
            remote=remote,
            branch=branch,
            credential_ref=credential_ref,
            operation="push",
        )
        if error:
            return OpsActionResult(False, "push", target_id=str(workspace_id or ""), error=error)
        assert workspace is not None and target is not None
        current = self.status(workspace.workspace_id)
        if current.detached:
            return self._failure(
                "push", workspace.workspace_id, "git_detached_head", "push requires an attached branch"
            )
        if current.conflict_count:
            return self._failure("push", workspace.workspace_id, "git_conflict", "push requires a conflict-free branch")
        if current.operation_state != "idle":
            return self._failure(
                "push",
                workspace.workspace_id,
                "git_operation_in_progress",
                "finish the active Git operation before push",
            )
        remote_name, branch_name = target
        arguments = {
            "workspace_id": workspace.workspace_id,
            "remote": remote_name,
            "branch": branch_name,
            "credential_ref": credential_ref,
        }
        blocked = self._authorize("git.push", "push", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        transport, transport_error = self._remote_transport_authorization(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation="push",
        )
        if transport_error:
            return OpsActionResult(
                False,
                "push",
                target_id=workspace.workspace_id,
                error=transport_error,
            )
        assert transport is not None
        result = self._git(
            hardened_git_transport_args(
                transport,
                ["push", "--porcelain", remote_name, f"HEAD:refs/heads/{branch_name}"],
                remote_name=remote_name,
            ),
            cwd=workspace.root,
            timeout_seconds=90,
        )
        return self._command_result("push", "git.push", workspace.workspace_id, arguments, result, approval_id)

    # --------------------------------------------------------------- internals

    def _workspace(self, workspace_id: str | None) -> tuple[WorkspaceRef | None, OpsError | None]:
        workspace = self._registry.resolve_workspace(workspace_id)
        if workspace is None:
            return None, OpsError("workspace_not_allowed", "workspace not registered")
        return workspace, None

    def _validate_repository(self, workspace: WorkspaceRef) -> OpsError | None:
        if not self._runner.exists("git"):
            return OpsError("git_not_found", "git binary not found")
        inside = self._git(["rev-parse", "--is-inside-work-tree"], cwd=workspace.root)
        if inside.timed_out:
            return OpsError("git_timeout", "git repository check timed out")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return OpsError("git_not_repository", "workspace is not a git repository")
        return None

    def _is_repository(self, root: Path) -> bool:
        if not self._runner.exists("git"):
            return False
        result = self._git(["rev-parse", "--is-inside-work-tree"], cwd=root)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _relative_path(self, workspace: WorkspaceRef, path: str | None) -> tuple[str, OpsError | None]:
        raw = str(path or "").strip()
        if not raw or Path(raw).is_absolute():
            return "", OpsError("path_not_allowed", "workspace-relative path required")
        resolved = self._registry.resolve_relative_path(workspace.workspace_id, raw)
        if resolved is None:
            return "", OpsError("path_not_allowed", "path escapes workspace")
        return resolved.relative_to(workspace.root).as_posix(), None

    def _mutation_paths(
        self, workspace_id: str | None, paths: Iterable[str]
    ) -> tuple[WorkspaceRef | None, list[str], OpsError | None]:
        workspace, error = self._workspace(workspace_id)
        if error:
            return None, [], error
        assert workspace is not None
        validation = self._validate_repository(workspace)
        if validation:
            return workspace, [], validation
        selected: list[str] = []
        for value in paths:
            normalized, path_error = self._relative_path(workspace, str(value or ""))
            if path_error:
                return workspace, [], path_error
            if normalized not in selected:
                selected.append(normalized)
        if not selected:
            return workspace, [], OpsError("path_not_allowed", "explicit paths required")
        if len(selected) > 500:
            return workspace, [], OpsError("path_not_allowed", "too many paths in one Git action")
        return workspace, selected, None

    def _path_mutation(
        self,
        workspace_id: str | None,
        paths: Iterable[str],
        *,
        action: str,
        tool_name: str,
        command,
        approval_id: str | None,
    ) -> OpsActionResult:
        workspace, selected, error = self._mutation_paths(workspace_id, paths)
        if error:
            return OpsActionResult(False, action, target_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        status = self.status(workspace.workspace_id)
        if status.error:
            return OpsActionResult(False, action, target_id=workspace.workspace_id, error=status.error)
        states = {item.path: item for item in status.changed_files}
        selected_states = [states.get(path) for path in selected]
        if any(item is None for item in selected_states):
            return self._failure(
                action,
                workspace.workspace_id,
                "git_path_state_invalid",
                "each path must be present in the current Git changes",
                paths=selected,
            )
        if action == "stage" and any(
            not (item.unstaged or item.untracked or item.conflicted) for item in selected_states
        ):
            return self._failure(
                action,
                workspace.workspace_id,
                "git_path_state_invalid",
                "stage requires unstaged, untracked or conflicted paths",
                paths=selected,
            )
        if action == "unstage" and any(not item.staged for item in selected_states):
            return self._failure(
                action,
                workspace.workspace_id,
                "git_path_state_invalid",
                "unstage requires staged paths",
                paths=selected,
            )
        arguments = {"workspace_id": workspace.workspace_id, "paths": selected}
        blocked = self._authorize(tool_name, action, workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        result = self._git(command(selected), cwd=workspace.root, timeout_seconds=30)
        return self._command_result(action, tool_name, workspace.workspace_id, arguments, result, approval_id)

    def _authorize(
        self,
        tool_name: str,
        action: str,
        target_id: str,
        arguments: dict[str, Any],
        approval_id: str | None,
    ) -> OpsActionResult | None:
        decision = self._policy.authorize(
            tool_name,
            action,
            target_id=target_id,
            arguments=arguments,
            approval_id=approval_id,
        )
        if decision.allowed:
            return None
        submitted_approval_id = str(approval_id or "").strip() or None
        request_id = None
        if decision.decision == "approval_required":
            request_id = (
                self._policy.create_approval_request(
                    tool_name=tool_name,
                    action=action,
                    target_id=target_id,
                    arguments=arguments,
                )
                or submitted_approval_id
            )
        code = "approval_required" if decision.decision == "approval_required" else "policy_denied"
        audit_ref = self._audit(
            f"ops_{tool_name.replace('.', '_')}_blocked",
            workspace_id=target_id,
            ok=False,
            decision=decision.decision,
            reason_code=decision.reason_code,
            approval_id=request_id or submitted_approval_id,
            paths=arguments.get("paths"),
        )
        return OpsActionResult(
            False,
            action,
            target_id=target_id,
            decision=decision.decision,
            approval_id=request_id,
            audit_ref=audit_ref,
            error=OpsError(code, decision.reason_code),
        )

    def _command_result(
        self,
        action: str,
        tool_name: str,
        workspace_id: str,
        arguments: dict[str, Any],
        result: CommandResult,
        approval_id: str | None,
    ) -> OpsActionResult:
        ok = result.returncode == 0 and not result.timed_out
        post_status = self.status(workspace_id) if ok else None
        audit_ref = self._audit(
            f"ops_{tool_name.replace('.', '_')}",
            workspace_id=workspace_id,
            ok=ok,
            returncode=result.returncode,
            approval_id=approval_id,
            paths=arguments.get("paths"),
            remote=arguments.get("remote"),
            branch=arguments.get("branch") or (post_status.branch if post_status else None),
            commit_sha=post_status.head_sha if post_status else None,
        )
        if ok:
            self._policy.consume_approval(approval_id)
        error = None
        if not ok:
            code = "git_timeout" if result.timed_out else "git_command_failed"
            error = OpsError(code, self._safe_git_message(result.stderr or "Git command failed"))
        metadata: dict[str, Any] = {"returncode": result.returncode, "output_truncated": result.truncated}
        if post_status is not None:
            metadata.update(
                {
                    "branch": post_status.branch,
                    "head_sha": post_status.head_sha,
                    "ahead": post_status.ahead,
                    "behind": post_status.behind,
                }
            )
        return OpsActionResult(
            ok,
            action,
            target_id=workspace_id,
            decision="allow",
            approval_id=approval_id,
            audit_ref=audit_ref,
            metadata=metadata,
            error=error,
        )

    def _failure(self, action: str, workspace_id: str, code: str, message: str, **details: Any) -> OpsActionResult:
        audit_ref = self._audit(
            f"ops_git_{action}_rejected",
            workspace_id=workspace_id,
            ok=False,
            reason_code=code,
            **details,
        )
        return OpsActionResult(
            False,
            action,
            target_id=workspace_id,
            decision="policy_denied" if code == "policy_denied" else "allow",
            audit_ref=audit_ref,
            error=OpsError(code, message),
        )

    def _sync_target(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        branch: str | None = None,
        credential_ref: str | None = None,
        operation: str,
    ) -> tuple[WorkspaceRef | None, tuple[str, str] | None, OpsError | None]:
        workspace, error = self._workspace(workspace_id)
        if error:
            return None, None, error
        assert workspace is not None
        status = self.status(workspace.workspace_id)
        if status.error:
            return workspace, None, status.error
        if status.detached or not status.branch:
            return workspace, None, OpsError("git_detached_head", "network Git actions require an attached branch")
        configured = {item.name for item in self.remotes(workspace.workspace_id).items}
        upstream_remote = next(
            (name for name in sorted(configured, key=len, reverse=True) if status.upstream.startswith(f"{name}/")),
            "",
        )
        upstream_branch = status.upstream[len(upstream_remote) + 1 :] if upstream_remote else ""
        remote_name = str(remote or upstream_remote).strip()
        branch_name = str(branch or upstream_branch or status.branch).strip()
        if not remote_name:
            return (
                workspace,
                None,
                OpsError("git_no_upstream", "no upstream remote is configured; select a registered remote"),
            )
        if not remote_name or remote_name not in configured or not _SAFE_REMOTE.fullmatch(remote_name):
            return workspace, None, OpsError("git_remote_not_allowed", "remote is not registered for this workspace")
        branch_check = self._git(["check-ref-format", "--branch", branch_name], cwd=workspace.root)
        if not branch_name or not _SAFE_BRANCH.fullmatch(branch_name) or branch_check.returncode != 0:
            return workspace, None, OpsError("git_branch_not_allowed", "branch is not a valid Git branch")
        if branch is not None and branch_name != (upstream_branch or status.branch):
            return (
                workspace,
                None,
                OpsError("git_branch_not_allowed", "only the current or configured upstream branch is allowed"),
            )
        if remote is not None and upstream_remote and remote_name != upstream_remote:
            return workspace, None, OpsError("git_remote_not_allowed", "only the configured upstream remote is allowed")
        policy_error = self._remote_access_error(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation=operation,
        )
        if policy_error is not None:
            return workspace, None, policy_error
        return workspace, (remote_name, branch_name), None

    def _fetch_target(
        self,
        workspace_id: str | None,
        *,
        remote: str | None,
        credential_ref: str | None,
    ) -> tuple[WorkspaceRef | None, str | None, OpsError | None]:
        workspace, error = self._workspace(workspace_id)
        if error:
            return None, None, error
        assert workspace is not None
        validation = self._validate_repository(workspace)
        if validation:
            return workspace, None, validation
        configured = {item.name for item in self.remotes(workspace.workspace_id).items}
        requested = str(remote or "").strip()
        if requested:
            if requested not in configured or not _SAFE_REMOTE.fullmatch(requested):
                return (
                    workspace,
                    None,
                    OpsError(
                        "git_remote_not_allowed",
                        "remote is not registered for this workspace",
                    ),
                )
            policy_error = self._remote_access_error(
                workspace=workspace,
                remote_name=requested,
                credential_ref=credential_ref,
                operation="fetch",
            )
            return workspace, requested, policy_error

        upstream_result = self._git(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=workspace.root,
        )
        upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else ""
        upstream_remote = next(
            (name for name in sorted(configured, key=len, reverse=True) if upstream.startswith(f"{name}/")),
            "",
        )
        if upstream_remote:
            policy_error = self._remote_access_error(
                workspace=workspace,
                remote_name=upstream_remote,
                credential_ref=credential_ref,
                operation="fetch",
            )
            return workspace, upstream_remote, policy_error
        if len(configured) == 1:
            selected = next(iter(configured))
            policy_error = self._remote_access_error(
                workspace=workspace,
                remote_name=selected,
                credential_ref=credential_ref,
                operation="fetch",
            )
            return workspace, selected, policy_error
        return (
            workspace,
            None,
            OpsError(
                "git_no_upstream",
                "select one of the registered remotes",
            ),
        )

    def _remote_access_error(
        self,
        *,
        workspace: WorkspaceRef,
        remote_name: str,
        credential_ref: str | None,
        operation: str,
    ) -> OpsError | None:
        _, error = self._remote_transport_authorization(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation=operation,
        )
        return error

    def _remote_transport_authorization(
        self,
        *,
        workspace: WorkspaceRef,
        remote_name: str,
        credential_ref: str | None,
        operation: str,
    ) -> tuple[GitTransportAuthorization | None, OpsError | None]:
        get_url_args = ["remote", "get-url"]
        if operation == "push":
            get_url_args.append("--push")
        get_url_args.append(remote_name)
        result = self._git(get_url_args, cwd=workspace.root)
        if result.returncode != 0:
            return None, OpsError(
                "git_remote_url_unavailable",
                "registered remote URL is unavailable",
            )
        try:
            request = GitRemotePolicyRequest(
                remote_url=result.stdout.strip(),
                operation=operation,
                credential_ref=credential_ref,
                allow_redirects=False,
                proxy_url=None,
                recurse_submodules=False,
                lfs_mode="pointer_only",
            )
            authorized = self._remote_policy.authorize(request)
            transport = GitTransportAuthorization.create(
                authorized=authorized,
                request=request,
            )
            transport.validate()
        except GitRemotePolicyError as exc:
            return None, OpsError(exc.reason_code, exc.reason_code)
        return transport, None

    def _changed_files(self, cwd: Path) -> tuple[list[GitChangedFile], bool]:
        result = self._git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=cwd)
        if result.returncode != 0:
            return [], result.truncated
        staged_stats = {item.path: item for item in self._diff_stats(cwd, "staged", "")}
        unstaged_stats = {item.path: item for item in self._diff_stats(cwd, "unstaged", "")}
        files: list[GitChangedFile] = []
        records = result.stdout.split("\0")
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if len(record) < 3:
                continue
            x, y = record[0], record[1]
            path = record[3:]
            original_path = ""
            if (x in {"R", "C"} or y in {"R", "C"}) and index < len(records):
                original_path = records[index]
                index += 1
            untracked = x == "?" and y == "?"
            staged = not untracked and x not in {" ", "?"}
            unstaged = not untracked and y not in {" ", "?"}
            conflicted = f"{x}{y}" in _CONFLICT_STATES
            left = staged_stats.get(path)
            right = unstaged_stats.get(path)
            additions = (left.additions if left else 0) + (right.additions if right else 0)
            deletions = (left.deletions if left else 0) + (right.deletions if right else 0)
            if untracked:
                additions = self._line_count(cwd / path, root=cwd)
            files.append(
                GitChangedFile(
                    path=path,
                    original_path=original_path,
                    index_status="?" if untracked else ("" if x == " " else x),
                    worktree_status="?" if untracked else ("" if y == " " else y),
                    staged=staged,
                    unstaged=unstaged,
                    untracked=untracked,
                    conflicted=conflicted,
                    renamed=x == "R" or y == "R",
                    deleted=x == "D" or y == "D",
                    binary=bool((left and left.binary) or (right and right.binary)),
                    additions=additions,
                    deletions=deletions,
                )
            )
        max_items = 1000
        return files[:max_items], result.truncated or len(files) > max_items

    def _diff_command(self, cwd: Path, scope: str, path: str) -> CommandResult:
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames"]
        if scope == "staged":
            args.extend(["--cached", "HEAD"])
        elif scope == "combined":
            args.append("HEAD")
        if path:
            args.extend(["--", self._literal_pathspec(path)])
        return self._git(args, cwd=cwd, timeout_seconds=15)

    def _diff_stats(self, cwd: Path, scope: str, path: str) -> list[GitDiffStat]:
        args = ["diff", "--numstat"]
        if scope == "staged":
            args.extend(["--cached", "HEAD"])
        elif scope == "combined":
            args.append("HEAD")
        if path:
            args.extend(["--", self._literal_pathspec(path)])
        result = self._git(args, cwd=cwd)
        if result.returncode != 0:
            return []
        items: list[GitDiffStat] = []
        for line in result.stdout.splitlines():
            additions, separator, rest = line.partition("\t")
            deletions, separator2, changed_path = rest.partition("\t")
            if not separator or not separator2:
                continue
            binary = additions == "-" or deletions == "-"
            items.append(
                GitDiffStat(
                    path=changed_path,
                    additions=0 if binary else int(additions or 0),
                    deletions=0 if binary else int(deletions or 0),
                    binary=binary,
                )
            )
        return items

    def _untracked_paths(self, cwd: Path, path_filter: str = "") -> list[str]:
        args = ["ls-files", "--others", "--exclude-standard"]
        if path_filter:
            args.extend(["--", self._literal_pathspec(path_filter)])
        result = self._git(args, cwd=cwd)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()][:200]

    def _untracked_diff(self, cwd: Path, path_filter: str) -> tuple[str, bool]:
        output: list[str] = []
        truncated = False
        for path in self._untracked_paths(cwd, path_filter)[:50]:
            try:
                (cwd / path).resolve().relative_to(cwd.resolve())
            except ValueError:
                continue
            result = self._git(
                ["diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", "--", "/dev/null", path],
                cwd=cwd,
            )
            if result.returncode not in {0, 1}:
                continue
            output.append(result.stdout)
            truncated = truncated or result.truncated
            if sum(len(item) for item in output) > 64_000:
                truncated = True
                break
        text = "".join(output)
        return text[:64_000], truncated or len(text) > 64_000

    def _untracked_stats(self, cwd: Path, path_filter: str) -> list[GitDiffStat]:
        return [
            GitDiffStat(path=path, additions=self._line_count(cwd / path, root=cwd))
            for path in self._untracked_paths(cwd, path_filter)
        ]

    @staticmethod
    def _line_count(path: Path, *, root: Path) -> int:
        try:
            resolved = path.resolve()
            resolved.relative_to(root.resolve())
            with resolved.open("rb") as handle:
                raw = handle.read(1_000_001)
            if len(raw) > 1_000_000:
                return 0
            if b"\x00" in raw:
                return 0
            return len(raw.decode("utf-8", errors="replace").splitlines())
        except (OSError, ValueError):
            return 0

    @staticmethod
    def _literal_pathspec(path: str) -> str:
        return f":(literal){path}"

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    @classmethod
    def _safe_git_message(cls, value: str) -> str:
        text = str(value or "Git command failed").replace("\x00", "").strip()
        text = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1***@", text)
        text = re.sub(r"(?i)(token|password|authorization)=([^&\s]+)", r"\1=***", text)
        return text[:240]

    def _ahead_behind(self, cwd: Path, upstream: str) -> tuple[int, int]:
        if not upstream:
            return 0, 0
        result = self._git(["rev-list", "--left-right", "--count", f"HEAD...{upstream}"], cwd=cwd)
        if result.returncode != 0:
            return 0, 0
        left, _, right = result.stdout.strip().partition("\t")
        try:
            return int(left or 0), int(right or 0)
        except ValueError:
            return 0, 0

    def _operation_state(self, cwd: Path) -> str:
        git_dir_result = self._git(["rev-parse", "--git-dir"], cwd=cwd)
        if git_dir_result.returncode != 0:
            return "unknown"
        git_dir = Path(git_dir_result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = (cwd / git_dir).resolve()
        checks = (
            ("MERGE_HEAD", "merge"),
            ("rebase-merge", "rebase"),
            ("rebase-apply", "rebase"),
            ("CHERRY_PICK_HEAD", "cherry-pick"),
            ("REVERT_HEAD", "revert"),
            ("BISECT_LOG", "bisect"),
        )
        return next((state for marker, state in checks if (git_dir / marker).exists()), "idle")

    @staticmethod
    def _commit_from_line(line: str, separator: str) -> GitCommitSummary | None:
        parts = line.split(separator)
        if len(parts) < 8:
            return None
        sha, short_sha, subject, author_name, author_email, authored_at, parents, refs = parts[:8]
        return GitCommitSummary(
            sha=sha,
            short_sha=short_sha,
            subject=subject,
            author_name=author_name,
            author_email=author_email,
            authored_at=authored_at,
            parents=[item for item in parents.split() if item],
            refs=[item.strip() for item in refs.split(",") if item.strip()],
        )

    def _reflog_activity(self, workspace: WorkspaceRef, limit: int) -> list[GitActivityEvent]:
        separator = "\x1f"
        result = self._git(
            [
                "reflog",
                "--all",
                f"--max-count={limit}",
                "--date=iso-strict",
                f"--format=%H{separator}%gd{separator}%gs{separator}%gn",
            ],
            cwd=workspace.root,
        )
        if result.returncode != 0:
            return []
        events: list[GitActivityEvent] = []
        for line in result.stdout.splitlines():
            parts = line.split(separator)
            if len(parts) < 4:
                continue
            sha, selector, summary, actor = parts[:4]
            timestamp_match = re.search(r"@\{(.+)\}$", selector)
            timestamp = timestamp_match.group(1) if timestamp_match else ""
            operation = summary.partition(":")[0].strip() or "reflog"
            events.append(
                GitActivityEvent(
                    id=f"reflog-{sha[:12]}",
                    timestamp=timestamp,
                    actor=actor or "git",
                    operation=operation,
                    action=operation,
                    outcome="observed",
                    source="git_reflog",
                    workspace_id=workspace.workspace_id,
                    summary=summary,
                )
            )
        return events

    @staticmethod
    def _audit_activity(workspace: WorkspaceRef, limit: int) -> list[GitActivityEvent]:
        try:
            from sqlmodel import Session, select

            from agent.database import engine
            from agent.db_models import AuditLogDB

            allowed = (
                "ops_git_stage",
                "ops_git_unstage",
                "ops_git_discard",
                "ops_git_commit",
                "ops_git_fetch",
                "ops_git_pull",
                "ops_git_push",
                "workspace_git_commit_push",
                "git_commit",
                "git_push",
            )
            with Session(engine) as session:
                rows = session.exec(
                    select(AuditLogDB)
                    .where(AuditLogDB.action.in_(allowed))  # type: ignore[attr-defined]
                    .order_by(AuditLogDB.timestamp.desc())  # type: ignore[attr-defined]
                    .limit(limit)
                ).all()
            events: list[GitActivityEvent] = []
            workspace_id = workspace.workspace_id
            workspace_fingerprint = git_workspace_fingerprint(workspace.root)
            for row in rows:
                details = dict(row.details or {})
                row_workspace = str(details.get("workspace_id") or "")
                row_fingerprint = str(details.get("workspace_fingerprint") or "")
                if row_workspace and row_workspace != workspace_id:
                    continue
                if row_fingerprint and row_fingerprint != workspace_fingerprint:
                    continue
                if not row_workspace and not row_fingerprint:
                    # Legacy global git_commit/git_push events cannot safely be
                    # attributed to a particular registered workspace.
                    continue
                timestamp = datetime.fromtimestamp(float(row.timestamp), tz=UTC).isoformat().replace("+00:00", "Z")
                explicit_outcome = str(details.get("outcome") or "").strip().lower()
                if explicit_outcome:
                    outcome = explicit_outcome
                elif "ok" in details:
                    outcome = "success" if bool(details.get("ok")) else "failed"
                else:
                    outcome = "observed"
                operation = str(
                    details.get("operation")
                    or ("commit_push" if row.action == "workspace_git_commit_push" else row.action)
                    or "git"
                )
                summary = str(details.get("summary") or "").strip()
                if not summary:
                    parts = [outcome]
                    if details.get("branch"):
                        parts.append(f"branch={details['branch']}")
                    commit_sha = str(details.get("commit_sha") or "")
                    if commit_sha:
                        parts.append(f"commit={commit_sha[:12]}")
                    summary = ", ".join(parts)
                events.append(
                    GitActivityEvent(
                        id=f"audit-{row.id}",
                        timestamp=timestamp,
                        actor=str(row.username or "ananta"),
                        operation=operation,
                        action=str(row.action or "git"),
                        outcome=outcome,
                        source="ananta_audit",
                        workspace_id=workspace_id,
                        task_id=str(row.task_id or ""),
                        goal_id=str(row.goal_id or ""),
                        trace_id=str(row.trace_id or ""),
                        approval_id=str(details.get("approval_id") or ""),
                        summary=summary or str(row.action or "Git action"),
                    )
                )
            return events
        except Exception:
            return []

    @staticmethod
    def _redact_remote_url(value: str) -> str:
        raw = str(value or "").strip()
        try:
            parsed = urlsplit(raw)
            if parsed.scheme and parsed.hostname:
                host = parsed.hostname or ""
                if parsed.port:
                    host = f"{host}:{parsed.port}"
                return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except ValueError:
            pass
        safe = raw.split("?", 1)[0].split("#", 1)[0]
        if "@" in safe and ":" in safe.split("@", 1)[0] and not safe.startswith("git@"):
            safe = f"***@{safe.split('@', 1)[1]}"
        return safe

    @staticmethod
    def _audit(action: str, **details: Any) -> str:
        audit_ref = f"git-{uuid.uuid4().hex[:16]}"
        log_audit(action, {"audit_ref": audit_ref, **details})
        return audit_ref

    def _git(self, args: list[str], *, cwd: Path, timeout_seconds: int | None = None) -> CommandResult:
        return self._runner.run(
            ["git", *args],
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=hardened_git_environment(),
        )


_default_git_ops_service: GitOpsService | None = None


def get_git_ops_service() -> GitOpsService:
    global _default_git_ops_service
    if _default_git_ops_service is None:
        _default_git_ops_service = GitOpsService()
    return _default_git_ops_service
