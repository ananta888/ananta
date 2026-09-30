"""Workspace-scoped Git read models (status, diff, history, branches, ...).

``GitOpsReadService`` owns the query side of Git Ops. It composes workspace
access, the repository inspector and the audit activity source; mutations,
policy and remote transport live in separate collaborators (SRP).
"""

from __future__ import annotations

import re
from typing import Any

from agent.services.git_ops_audit_activity import GitAuditActivitySource
from agent.services.git_ops_repository_inspector import GitRepositoryInspector
from agent.services.git_ops_workspace_access import (
    GitCommandExecutor,
    GitWorkspaceAccess,
    bounded_int,
    literal_pathspec,
    redact_remote_url,
    upstream_remote_name,
)
from agent.services.ops_models import (
    GitActivity,
    GitActivityEvent,
    GitBranch,
    GitBranches,
    GitChanges,
    GitDiff,
    GitHistory,
    GitRemote,
    GitRemotes,
    GitStatus,
    OpsError,
)

_FIELD_SEPARATOR = "\x1f"


class GitOpsReadService:
    """Read-only Git projections for registered workspaces."""

    def __init__(
        self,
        *,
        access: GitWorkspaceAccess,
        git: GitCommandExecutor,
        inspector: GitRepositoryInspector,
        audit_activity: GitAuditActivitySource,
    ) -> None:
        self._access = access
        self._git = git
        self._inspector = inspector
        self._audit_activity = audit_activity

    def workspaces(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for ref in self._access.registered_workspaces():
            repository = self._access.is_repository(ref.root)
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
        workspace, error = self._access.resolve(workspace_id)
        if error:
            return GitStatus(workspace_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = self._access.validate_repository(workspace)
        if validation:
            return GitStatus(workspace_id=workspace.workspace_id, error=validation)

        branch_result = self._git.run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=workspace.root)
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
        head_sha = self._inspector.head_sha(workspace.root)
        upstream = self._inspector.upstream(workspace.root)
        configured_remotes = {item.name for item in self.remotes(workspace.workspace_id).items}
        remote_name = upstream_remote_name(upstream, configured_remotes)
        ahead, behind = self._inspector.ahead_behind(workspace.root, upstream)
        changed_files, truncated = self._inspector.changed_files(workspace.root)
        staged_count = sum(1 for item in changed_files if item.staged)
        unstaged_count = sum(1 for item in changed_files if item.unstaged)
        untracked_count = sum(1 for item in changed_files if item.untracked)
        conflict_count = sum(1 for item in changed_files if item.conflicted)
        operation_state = self._inspector.operation_state(workspace.root)
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
        workspace, error = self._access.resolve(workspace_id)
        selected_scope = str(scope or ("staged" if cached else "unstaged")).strip().lower()

        def failed(error_value: OpsError, *, workspace_key: str, path_value: str) -> GitDiff:
            return GitDiff(
                workspace_id=workspace_key,
                cached=cached,
                path=path_value,
                scope=selected_scope,
                error=error_value,
            )

        if selected_scope not in {"staged", "unstaged", "combined"}:
            return failed(
                OpsError("git_command_failed", "scope must be staged, unstaged or combined"),
                workspace_key=str(workspace_id or ""),
                path_value=str(path or ""),
            )
        if error:
            return failed(error, workspace_key=str(workspace_id or ""), path_value=str(path or ""))
        assert workspace is not None
        validation = self._access.validate_repository(workspace)
        if validation:
            return failed(validation, workspace_key=workspace.workspace_id, path_value=str(path or ""))
        normalized_path, path_error = self._access.relative_path(workspace, path) if path else ("", None)
        if path_error:
            return failed(path_error, workspace_key=workspace.workspace_id, path_value=str(path or ""))

        staged = self._inspector.diff_command(workspace.root, "staged", normalized_path)
        unstaged = self._inspector.diff_command(workspace.root, "unstaged", normalized_path)
        combined = self._inspector.diff_command(workspace.root, "combined", normalized_path)
        for result in (staged, unstaged, combined):
            if result.timed_out:
                return failed(
                    OpsError("git_timeout", "git diff timed out"),
                    workspace_key=workspace.workspace_id,
                    path_value=normalized_path,
                )
            if result.returncode != 0:
                return failed(
                    OpsError("git_command_failed", result.stderr[:240]),
                    workspace_key=workspace.workspace_id,
                    path_value=normalized_path,
                )

        untracked_diff, untracked_truncated = self._inspector.untracked_diff(workspace.root, normalized_path)
        unstaged_text = unstaged.stdout + untracked_diff
        combined_text = combined.stdout + untracked_diff
        selected = {"staged": staged.stdout, "unstaged": unstaged_text, "combined": combined_text}[selected_scope]
        stats = self._inspector.diff_stats(workspace.root, selected_scope, normalized_path)
        if selected_scope in {"unstaged", "combined"}:
            stats.extend(self._inspector.untracked_stats(workspace.root, normalized_path))
        additions = sum(item.additions for item in stats)
        deletions = sum(item.deletions for item in stats)
        return GitDiff(
            workspace_id=workspace.workspace_id,
            cached=selected_scope == "staged",
            path=normalized_path,
            scope=selected_scope,
            head_sha=self._inspector.head_sha(workspace.root),
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
        workspace, error = self._access.resolve(workspace_id)
        safe_limit = bounded_int(limit, default=50, minimum=1, maximum=200)
        safe_offset = bounded_int(offset, default=0, minimum=0, maximum=100_000)
        if error:
            return GitHistory(workspace_id=str(workspace_id or ""), limit=safe_limit, offset=safe_offset, error=error)
        assert workspace is not None
        validation = self._access.validate_repository(workspace)
        if validation:
            return GitHistory(
                workspace_id=workspace.workspace_id, limit=safe_limit, offset=safe_offset, error=validation
            )
        normalized_path, path_error = self._access.relative_path(workspace, path) if path else ("", None)
        if path_error:
            return GitHistory(
                workspace_id=workspace.workspace_id, limit=safe_limit, offset=safe_offset, error=path_error
            )
        separator = _FIELD_SEPARATOR
        args = [
            "log",
            "--all",
            f"--max-count={safe_limit + 1}",
            f"--skip={safe_offset}",
            "--date=iso-strict",
            f"--pretty=format:%H{separator}%h{separator}%s{separator}%an{separator}%ae{separator}%aI{separator}%P{separator}%D",
        ]
        if normalized_path:
            args.extend(["--", literal_pathspec(normalized_path)])
        result = self._git.run(args, cwd=workspace.root)
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
        parsed = [
            self._inspector.commit_from_line(line, separator) for line in result.stdout.splitlines() if line.strip()
        ]
        items = [item for item in parsed if item is not None]
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
        workspace, error = self._access.resolve(workspace_id)
        if error:
            return GitBranches(workspace_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = self._access.validate_repository(workspace)
        if validation:
            return GitBranches(workspace_id=workspace.workspace_id, error=validation)
        separator = _FIELD_SEPARATOR
        result = self._git.run(
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
            ahead, behind = (0, 0) if is_remote else self._inspector.ahead_behind(workspace.root, upstream)
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
        workspace, error = self._access.resolve(workspace_id)
        if error:
            return GitRemotes(workspace_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = self._access.validate_repository(workspace)
        if validation:
            return GitRemotes(workspace_id=workspace.workspace_id, error=validation)
        result = self._git.run(["remote", "-v"], cwd=workspace.root)
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
            values.setdefault(name, {})[kind] = redact_remote_url(url)
        items = [
            GitRemote(name=name, fetch_url=value.get("fetch", ""), push_url=value.get("push", ""))
            for name, value in sorted(values.items())
        ]
        return GitRemotes(workspace_id=workspace.workspace_id, items=items, count=len(items))

    def activity(self, workspace_id: str | None = None, *, limit: int = 100) -> GitActivity:
        workspace, error = self._access.resolve(workspace_id)
        safe_limit = bounded_int(limit, default=100, minimum=1, maximum=300)
        if error:
            return GitActivity(workspace_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = self._access.validate_repository(workspace)
        if validation:
            return GitActivity(workspace_id=workspace.workspace_id, error=validation)
        events = self._inspector.reflog_activity(workspace, safe_limit)
        events.extend(self._audit_activity.events(workspace, safe_limit))
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
