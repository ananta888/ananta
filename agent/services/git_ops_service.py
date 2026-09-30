"""Hub-side, workspace-scoped Git control surface (public entry point).

``GitOpsService`` is a thin facade that composes the Git Ops collaborators:

* ``GitWorkspaceAccess`` / ``GitCommandExecutor`` - workspace + command seam
* ``GitOpsReadService`` - status, diff, history, branches, remotes, activity
* ``GitOpsActionGate`` - policy authorization and audited results
* ``GitRemoteTargetResolver`` - upstream/remote selection and transport policy
* ``GitOpsMutationService`` - stage, unstage, discard, commit, fetch, pull, push

Each collaborator can be replaced through a keyword-only constructor
argument; production defaults are built from the registry, command runner,
Ops policy and remote access policy exactly as before.
"""

from __future__ import annotations

from typing import Any, Iterable

from agent.services.git_ops_action_gate import GitOpsActionGate
from agent.services.git_ops_audit_activity import (
    AuditLogGitActivitySource,
    GitAuditActivitySource,
    GitAuditRecorder,
    record_git_audit,
)
from agent.services.git_ops_mutation_service import GitOpsMutationService
from agent.services.git_ops_read_service import GitOpsReadService
from agent.services.git_ops_remote_targets import GitRemoteTargetResolver
from agent.services.git_ops_repository_inspector import GitRepositoryInspector
from agent.services.git_ops_workspace_access import GitCommandExecutor, GitWorkspaceAccess
from agent.services.git_remote_policy_service import GitRemoteAccessPolicyPort, get_git_remote_access_policy
from agent.services.ops_command_runner import CommandRunner, get_default_command_runner
from agent.services.ops_models import (
    GitActivity,
    GitBranches,
    GitChanges,
    GitDiff,
    GitHistory,
    GitRemotes,
    GitStatus,
    OpsActionResult,
)
from agent.services.ops_policy_service import OpsPolicyService, get_ops_policy_service
from agent.services.ops_registry_service import OpsRegistryService, get_ops_registry_service

__all__ = ["GitOpsService", "get_git_ops_service"]


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
        audit_activity: GitAuditActivitySource | None = None,
        audit_recorder: GitAuditRecorder = record_git_audit,
    ) -> None:
        git = GitCommandExecutor(runner or get_default_command_runner())
        access = GitWorkspaceAccess(registry=registry or get_ops_registry_service(), git=git)
        self._reads = GitOpsReadService(
            access=access,
            git=git,
            inspector=GitRepositoryInspector(git),
            audit_activity=audit_activity or AuditLogGitActivitySource(),
        )
        gate = GitOpsActionGate(
            policy=policy or get_ops_policy_service(),
            status_reader=self._reads,
            audit=audit_recorder,
        )
        targets = GitRemoteTargetResolver(
            access=access,
            git=git,
            state_reader=self._reads,
            remote_policy=remote_policy or get_git_remote_access_policy(),
        )
        self._mutations = GitOpsMutationService(
            access=access,
            git=git,
            status_reader=self._reads,
            gate=gate,
            targets=targets,
        )

    # ------------------------------------------------------------------ reads

    def workspaces(self) -> list[dict[str, Any]]:
        return self._reads.workspaces()

    def status(self, workspace_id: str | None = None) -> GitStatus:
        return self._reads.status(workspace_id)

    def changes(self, workspace_id: str | None = None) -> GitChanges:
        return self._reads.changes(workspace_id)

    def diff(
        self,
        workspace_id: str | None = None,
        *,
        path: str | None = None,
        cached: bool = False,
        scope: str | None = None,
    ) -> GitDiff:
        return self._reads.diff(workspace_id, path=path, cached=cached, scope=scope)

    def history(
        self,
        workspace_id: str | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
        path: str | None = None,
    ) -> GitHistory:
        return self._reads.history(workspace_id, limit=limit, offset=offset, path=path)

    def branches(self, workspace_id: str | None = None) -> GitBranches:
        return self._reads.branches(workspace_id)

    def remotes(self, workspace_id: str | None = None) -> GitRemotes:
        return self._reads.remotes(workspace_id)

    def activity(self, workspace_id: str | None = None, *, limit: int = 100) -> GitActivity:
        return self._reads.activity(workspace_id, limit=limit)

    # --------------------------------------------------------------- mutations

    def stage(
        self,
        workspace_id: str | None,
        paths: Iterable[str],
        *,
        staged: bool = True,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        return self._mutations.stage(workspace_id, paths, staged=staged, approval_id=approval_id)

    def unstage(
        self, workspace_id: str | None, paths: Iterable[str], *, approval_id: str | None = None
    ) -> OpsActionResult:
        return self._mutations.unstage(workspace_id, paths, approval_id=approval_id)

    def discard(
        self, workspace_id: str | None, paths: Iterable[str], *, approval_id: str | None = None
    ) -> OpsActionResult:
        return self._mutations.discard(workspace_id, paths, approval_id=approval_id)

    def commit(self, workspace_id: str | None, message: str, *, approval_id: str | None = None) -> OpsActionResult:
        return self._mutations.commit(workspace_id, message, approval_id=approval_id)

    def fetch(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        credential_ref: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        return self._mutations.fetch(
            workspace_id, remote=remote, credential_ref=credential_ref, approval_id=approval_id
        )

    def pull(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        branch: str | None = None,
        credential_ref: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        return self._mutations.pull(
            workspace_id, remote=remote, branch=branch, credential_ref=credential_ref, approval_id=approval_id
        )

    def push(
        self,
        workspace_id: str | None,
        *,
        remote: str | None = None,
        branch: str | None = None,
        credential_ref: str | None = None,
        approval_id: str | None = None,
    ) -> OpsActionResult:
        return self._mutations.push(
            workspace_id, remote=remote, branch=branch, credential_ref=credential_ref, approval_id=approval_id
        )


_default_git_ops_service: GitOpsService | None = None


def get_git_ops_service() -> GitOpsService:
    global _default_git_ops_service
    if _default_git_ops_service is None:
        _default_git_ops_service = GitOpsService()
    return _default_git_ops_service
