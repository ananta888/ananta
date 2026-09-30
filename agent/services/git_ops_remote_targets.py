"""Remote/branch target resolution and transport authorization for Git Ops.

Network Git actions (fetch, pull, push) may only address the registered
upstream or an explicitly registered remote, and only after the remote
access policy has authorized a hardened transport. That decision is owned by
``GitRemoteTargetResolver`` (SRP); it reads repository state through the
small ``GitStateReader`` port instead of the full service (ISP).
"""

from __future__ import annotations

from typing import Protocol

from agent.services.git_ops_workspace_access import (
    SAFE_BRANCH,
    SAFE_REMOTE,
    GitCommandExecutor,
    GitWorkspaceAccess,
    upstream_remote_name,
)
from agent.services.git_remote_policy_service import (
    GitRemoteAccessPolicyPort,
    GitRemotePolicyError,
    GitRemotePolicyRequest,
    GitTransportAuthorization,
)
from agent.services.ops_models import GitRemotes, GitStatus, OpsError
from agent.services.ops_registry_service import WorkspaceRef


class GitStateReader(Protocol):
    def status(self, workspace_id: str | None = None) -> GitStatus: ...

    def remotes(self, workspace_id: str | None = None) -> GitRemotes: ...


class GitRemoteTargetResolver:
    """Selects allowed remote/branch targets and authorizes their transport."""

    def __init__(
        self,
        *,
        access: GitWorkspaceAccess,
        git: GitCommandExecutor,
        state_reader: GitStateReader,
        remote_policy: GitRemoteAccessPolicyPort,
    ) -> None:
        self._access = access
        self._git = git
        self._state_reader = state_reader
        self._remote_policy = remote_policy

    def sync_target(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        branch: str | None = None,
        credential_ref: str | None = None,
        operation: str,
    ) -> tuple[WorkspaceRef | None, tuple[str, str] | None, OpsError | None]:
        workspace, error = self._access.resolve(workspace_id)
        if error:
            return None, None, error
        assert workspace is not None
        status = self._state_reader.status(workspace.workspace_id)
        if status.error:
            return workspace, None, status.error
        if status.detached or not status.branch:
            return workspace, None, OpsError("git_detached_head", "network Git actions require an attached branch")
        configured = {item.name for item in self._state_reader.remotes(workspace.workspace_id).items}
        upstream_remote = upstream_remote_name(status.upstream, configured)
        upstream_branch = status.upstream[len(upstream_remote) + 1 :] if upstream_remote else ""
        remote_name = str(remote or upstream_remote).strip()
        branch_name = str(branch or upstream_branch or status.branch).strip()
        if not remote_name:
            return (
                workspace,
                None,
                OpsError("git_no_upstream", "no upstream remote is configured; select a registered remote"),
            )
        if not remote_name or remote_name not in configured or not SAFE_REMOTE.fullmatch(remote_name):
            return workspace, None, OpsError("git_remote_not_allowed", "remote is not registered for this workspace")
        branch_check = self._git.run(["check-ref-format", "--branch", branch_name], cwd=workspace.root)
        if not branch_name or not SAFE_BRANCH.fullmatch(branch_name) or branch_check.returncode != 0:
            return workspace, None, OpsError("git_branch_not_allowed", "branch is not a valid Git branch")
        if branch is not None and branch_name != (upstream_branch or status.branch):
            return (
                workspace,
                None,
                OpsError("git_branch_not_allowed", "only the current or configured upstream branch is allowed"),
            )
        if remote is not None and upstream_remote and remote_name != upstream_remote:
            return workspace, None, OpsError("git_remote_not_allowed", "only the configured upstream remote is allowed")
        policy_error = self.remote_access_error(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation=operation,
        )
        if policy_error is not None:
            return workspace, None, policy_error
        return workspace, (remote_name, branch_name), None

    def fetch_target(
        self,
        workspace_id: str | None,
        *,
        remote: str | None,
        credential_ref: str | None,
    ) -> tuple[WorkspaceRef | None, str | None, OpsError | None]:
        workspace, error = self._access.resolve(workspace_id)
        if error:
            return None, None, error
        assert workspace is not None
        validation = self._access.validate_repository(workspace)
        if validation:
            return workspace, None, validation
        configured = {item.name for item in self._state_reader.remotes(workspace.workspace_id).items}
        requested = str(remote or "").strip()
        if requested:
            if requested not in configured or not SAFE_REMOTE.fullmatch(requested):
                return (
                    workspace,
                    None,
                    OpsError(
                        "git_remote_not_allowed",
                        "remote is not registered for this workspace",
                    ),
                )
            return workspace, requested, self._fetch_policy_error(workspace, requested, credential_ref)

        upstream_result = self._git.run(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=workspace.root,
        )
        upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else ""
        upstream_remote = upstream_remote_name(upstream, configured)
        if upstream_remote:
            return workspace, upstream_remote, self._fetch_policy_error(workspace, upstream_remote, credential_ref)
        if len(configured) == 1:
            selected = next(iter(configured))
            return workspace, selected, self._fetch_policy_error(workspace, selected, credential_ref)
        return (
            workspace,
            None,
            OpsError(
                "git_no_upstream",
                "select one of the registered remotes",
            ),
        )

    def _fetch_policy_error(
        self, workspace: WorkspaceRef, remote_name: str, credential_ref: str | None
    ) -> OpsError | None:
        return self.remote_access_error(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation="fetch",
        )

    def remote_access_error(
        self,
        *,
        workspace: WorkspaceRef,
        remote_name: str,
        credential_ref: str | None,
        operation: str,
    ) -> OpsError | None:
        _, error = self.transport_authorization(
            workspace=workspace,
            remote_name=remote_name,
            credential_ref=credential_ref,
            operation=operation,
        )
        return error

    def transport_authorization(
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
        result = self._git.run(get_url_args, cwd=workspace.root)
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
