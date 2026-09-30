"""Policy authorization and audited result shaping for Git Ops mutations.

``GitOpsActionGate`` is the single place where a Git mutation is authorized
against the Ops policy, rejected with an audit trail, or turned into an
``OpsActionResult`` after the command ran. Mutation flows depend on it
instead of on the policy service and audit sink directly (SRP, DIP).
"""

from __future__ import annotations

from typing import Any, Protocol

from agent.services.git_ops_audit_activity import GitAuditRecorder
from agent.services.git_ops_workspace_access import safe_git_message
from agent.services.ops_command_runner import CommandResult
from agent.services.ops_models import GitStatus, OpsActionResult, OpsError


class GitOpsPolicyPort(Protocol):
    """The subset of ``OpsPolicyService`` used by Git Ops mutations."""

    def authorize(
        self,
        tool_name: str,
        action: str,
        *,
        target_id: str,
        arguments: dict[str, Any],
        approval_id: str | None,
    ) -> Any: ...

    def create_approval_request(
        self,
        *,
        tool_name: str,
        action: str,
        target_id: str,
        arguments: dict[str, Any],
    ) -> str | None: ...

    def consume_approval(self, approval_id: str | None) -> Any: ...


class GitStatusReader(Protocol):
    def status(self, workspace_id: str | None = None) -> GitStatus: ...


class GitOpsActionGate:
    """Authorizes Git mutations and produces audited action results."""

    def __init__(
        self,
        *,
        policy: GitOpsPolicyPort,
        status_reader: GitStatusReader,
        audit: GitAuditRecorder,
    ) -> None:
        self._policy = policy
        self._status_reader = status_reader
        self._audit = audit

    def authorize(
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

    def command_result(
        self,
        action: str,
        tool_name: str,
        workspace_id: str,
        arguments: dict[str, Any],
        result: CommandResult,
        approval_id: str | None,
    ) -> OpsActionResult:
        ok = result.returncode == 0 and not result.timed_out
        post_status = self._status_reader.status(workspace_id) if ok else None
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
            error = OpsError(code, safe_git_message(result.stderr or "Git command failed"))
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

    def failure(self, action: str, workspace_id: str, code: str, message: str, **details: Any) -> OpsActionResult:
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
