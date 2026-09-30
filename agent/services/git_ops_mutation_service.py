"""Governed Git mutations (stage, unstage, discard, commit, fetch, pull, push).

``GitOpsMutationService`` owns the command side of Git Ops. Every mutation
validates explicit paths or the registered upstream, is authorized through
``GitOpsActionGate`` and executes a fixed hardened ``git`` command; clients
never supply refs, remotes or shell fragments (SRP). Remote target selection
is delegated to ``GitRemoteTargetResolver``.
"""

from __future__ import annotations

from typing import Callable, Iterable

from agent.services.commit_message_validator import CommitMessageValidator
from agent.services.git_ops_action_gate import GitOpsActionGate, GitStatusReader
from agent.services.git_ops_remote_targets import GitRemoteTargetResolver
from agent.services.git_ops_workspace_access import GitCommandExecutor, GitWorkspaceAccess, literal_pathspec
from agent.services.git_remote_policy_service import hardened_git_transport_args
from agent.services.ops_models import OpsActionResult


class GitOpsMutationService:
    """Executes policy-gated, workspace-scoped Git mutations."""

    def __init__(
        self,
        *,
        access: GitWorkspaceAccess,
        git: GitCommandExecutor,
        status_reader: GitStatusReader,
        gate: GitOpsActionGate,
        targets: GitRemoteTargetResolver,
        commit_message_validator: Callable[[], CommitMessageValidator] = CommitMessageValidator,
    ) -> None:
        self._access = access
        self._git = git
        self._status_reader = status_reader
        self._gate = gate
        self._targets = targets
        self._commit_message_validator = commit_message_validator

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
            command=lambda selected: ["add", "--", *[literal_pathspec(path) for path in selected]],
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
                *[literal_pathspec(path) for path in selected],
            ],
            approval_id=approval_id,
        )

    def discard(
        self, workspace_id: str | None, paths: Iterable[str], *, approval_id: str | None = None
    ) -> OpsActionResult:
        workspace, selected, error = self._access.mutation_paths(workspace_id, paths)
        if error:
            return OpsActionResult(False, "discard", target_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        status = self._status_reader.status(workspace.workspace_id)
        if status.error:
            return OpsActionResult(False, "discard", target_id=workspace.workspace_id, error=status.error)
        state_by_path = {item.path: item for item in status.changed_files}
        selected_states = [state_by_path.get(path) for path in selected]
        rejection = _discard_rejection(selected_states)
        if rejection is not None:
            code, message = rejection
            return self._gate.failure("discard", workspace.workspace_id, code, message, paths=selected)
        arguments = {"workspace_id": workspace.workspace_id, "paths": selected}
        blocked = self._gate.authorize("git.discard", "discard", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        result = self._git.run(
            ["restore", "--worktree", "--", *[literal_pathspec(path) for path in selected]],
            cwd=workspace.root,
        )
        return self._gate.command_result(
            "discard", "git.discard", workspace.workspace_id, arguments, result, approval_id
        )

    def commit(self, workspace_id: str | None, message: str, *, approval_id: str | None = None) -> OpsActionResult:
        workspace, error = self._access.resolve(workspace_id)
        if error:
            return OpsActionResult(False, "commit", target_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        validation = self._commit_message_validator().validate(str(message or ""))
        if not validation.valid:
            return self._gate.failure(
                "commit",
                workspace.workspace_id,
                "invalid_commit_message",
                "invalid commit message",
                errors=validation.errors,
            )
        status = self._status_reader.status(workspace.workspace_id)
        if status.error:
            return OpsActionResult(False, "commit", target_id=workspace.workspace_id, error=status.error)
        if status.conflict_count:
            return self._gate.failure(
                "commit", workspace.workspace_id, "git_conflict", "conflicts must be resolved before commit"
            )
        if status.operation_state != "idle":
            return self._gate.failure(
                "commit",
                workspace.workspace_id,
                "git_operation_in_progress",
                "finish the active Git operation before commit",
            )
        if not status.staged_count:
            return self._gate.failure(
                "commit", workspace.workspace_id, "git_nothing_to_commit", "no staged changes to commit"
            )
        arguments = {"workspace_id": workspace.workspace_id, "message": str(message)}
        blocked = self._gate.authorize("git.commit", "commit", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        result = self._git.run(["commit", "-m", str(message)], cwd=workspace.root, timeout_seconds=30)
        return self._gate.command_result("commit", "git.commit", workspace.workspace_id, arguments, result, approval_id)

    def fetch(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        credential_ref: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        workspace, remote_name, error = self._targets.fetch_target(
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
        blocked = self._gate.authorize("git.fetch", "fetch", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        transport, transport_error = self._targets.transport_authorization(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation="fetch",
        )
        if transport_error:
            return OpsActionResult(False, "fetch", target_id=workspace.workspace_id, error=transport_error)
        assert transport is not None
        result = self._git.run(
            hardened_git_transport_args(
                transport,
                ["fetch", "--no-tags", "--no-recurse-submodules", remote_name],
                remote_name=remote_name,
            ),
            cwd=workspace.root,
            timeout_seconds=60,
        )
        return self._gate.command_result("fetch", "git.fetch", workspace.workspace_id, arguments, result, approval_id)

    def pull(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        branch: str | None = None,
        credential_ref: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        workspace, target, error = self._targets.sync_target(
            workspace_id,
            remote=remote,
            branch=branch,
            credential_ref=credential_ref,
            operation="pull",
        )
        if error:
            return OpsActionResult(False, "pull", target_id=str(workspace_id or ""), error=error)
        assert workspace is not None and target is not None
        current = self._status_reader.status(workspace.workspace_id)
        if current.dirty:
            return self._gate.failure(
                "pull", workspace.workspace_id, "git_dirty_worktree", "pull requires a clean worktree"
            )
        if current.operation_state != "idle":
            return self._gate.failure(
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
        blocked = self._gate.authorize("git.pull", "pull_ff_only", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        transport, transport_error = self._targets.transport_authorization(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation="pull",
        )
        if transport_error:
            return OpsActionResult(False, "pull", target_id=workspace.workspace_id, error=transport_error)
        assert transport is not None
        result = self._git.run(
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
        return self._gate.command_result("pull", "git.pull", workspace.workspace_id, arguments, result, approval_id)

    def push(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        branch: str | None = None,
        credential_ref: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        workspace, target, error = self._targets.sync_target(
            workspace_id,
            remote=remote,
            branch=branch,
            credential_ref=credential_ref,
            operation="push",
        )
        if error:
            return OpsActionResult(False, "push", target_id=str(workspace_id or ""), error=error)
        assert workspace is not None and target is not None
        current = self._status_reader.status(workspace.workspace_id)
        if current.detached:
            return self._gate.failure(
                "push", workspace.workspace_id, "git_detached_head", "push requires an attached branch"
            )
        if current.conflict_count:
            return self._gate.failure(
                "push", workspace.workspace_id, "git_conflict", "push requires a conflict-free branch"
            )
        if current.operation_state != "idle":
            return self._gate.failure(
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
        blocked = self._gate.authorize("git.push", "push", workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        transport, transport_error = self._targets.transport_authorization(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation="push",
        )
        if transport_error:
            return OpsActionResult(False, "push", target_id=workspace.workspace_id, error=transport_error)
        assert transport is not None
        result = self._git.run(
            hardened_git_transport_args(
                transport,
                ["push", "--porcelain", remote_name, f"HEAD:refs/heads/{branch_name}"],
                remote_name=remote_name,
            ),
            cwd=workspace.root,
            timeout_seconds=90,
        )
        return self._gate.command_result("push", "git.push", workspace.workspace_id, arguments, result, approval_id)

    def _path_mutation(
        self,
        workspace_id: str | None,
        paths: Iterable[str],
        *,
        action: str,
        tool_name: str,
        command: Callable[[list[str]], list[str]],
        approval_id: str | None,
    ) -> OpsActionResult:
        workspace, selected, error = self._access.mutation_paths(workspace_id, paths)
        if error:
            return OpsActionResult(False, action, target_id=str(workspace_id or ""), error=error)
        assert workspace is not None
        status = self._status_reader.status(workspace.workspace_id)
        if status.error:
            return OpsActionResult(False, action, target_id=workspace.workspace_id, error=status.error)
        states = {item.path: item for item in status.changed_files}
        selected_states = [states.get(path) for path in selected]
        if any(item is None for item in selected_states):
            return self._gate.failure(
                action,
                workspace.workspace_id,
                "git_path_state_invalid",
                "each path must be present in the current Git changes",
                paths=selected,
            )
        if action == "stage" and any(
            not (item.unstaged or item.untracked or item.conflicted) for item in selected_states
        ):
            return self._gate.failure(
                action,
                workspace.workspace_id,
                "git_path_state_invalid",
                "stage requires unstaged, untracked or conflicted paths",
                paths=selected,
            )
        if action == "unstage" and any(not item.staged for item in selected_states):
            return self._gate.failure(
                action,
                workspace.workspace_id,
                "git_path_state_invalid",
                "unstage requires staged paths",
                paths=selected,
            )
        arguments = {"workspace_id": workspace.workspace_id, "paths": selected}
        blocked = self._gate.authorize(tool_name, action, workspace.workspace_id, arguments, approval_id)
        if blocked:
            return blocked
        result = self._git.run(command(selected), cwd=workspace.root, timeout_seconds=30)
        return self._gate.command_result(action, tool_name, workspace.workspace_id, arguments, result, approval_id)


def _discard_rejection(selected_states: list) -> tuple[str, str] | None:
    """Return the first reason code/message that forbids discarding the paths."""

    if any(item is None for item in selected_states):
        return "git_path_state_invalid", "each path must be present in the current Git changes"
    if any(item.untracked for item in selected_states):
        return "git_untracked_discard_denied", "untracked files are never deleted by Git Ops"
    if any(not item.unstaged for item in selected_states):
        return "git_path_state_invalid", "discard requires tracked unstaged changes"
    if any(item.conflicted for item in selected_states):
        return "git_conflict", "conflicted paths must be resolved explicitly"
    if any(item.renamed for item in selected_states):
        return "git_path_state_invalid", "renames must be unstaged or resolved explicitly"
    return None
