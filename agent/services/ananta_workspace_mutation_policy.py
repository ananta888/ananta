"""AWWPI-004/008/013: workspace mutation policy for the ananta-worker.

The policy decides (a) which ``mutation_mode`` a task runs in (explicit
config, task_kind mapping, risk escalation to ``strict_patch_request``)
and (b) whether the files a worker changed are acceptable: only
explicitly materialized or policy-allowed files may change; ``.git``,
``.ananta``, ``rag_helper``, secrets, absolute paths and path traversal
are always blocked. Deletions/renames surface as blocked changes until
they get their own approval stage (see rollout plan).

Docs: ``docs/security/ananta-worker-workspace-mutation-policy.md``.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MUTATION_MODE_READ_ONLY = "read_only"
MUTATION_MODE_CONTROLLED_WORKSPACE = "controlled_workspace"
MUTATION_MODE_STRICT_PATCH_REQUEST = "strict_patch_request"
MUTATION_MODE_EXTERNAL_AGENT_WORKSPACE = "external_agent_workspace"

VALID_MUTATION_MODES = {
    MUTATION_MODE_READ_ONLY,
    MUTATION_MODE_CONTROLLED_WORKSPACE,
    MUTATION_MODE_STRICT_PATCH_REQUEST,
    MUTATION_MODE_EXTERNAL_AGENT_WORKSPACE,
}

_ALWAYS_FORBIDDEN_SEGMENTS = {".git", ".ananta", "rag_helper", "__pycache__"}
_SECRET_NAME_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*secret*", "*credential*", ".npmrc", ".netrc",
)
# AWWPI-004: paths that force strict_patch_request even in controlled mode.
_DEFAULT_STRICT_PATH_MARKERS = (
    "auth", "oidc", "keycloak", "deployment", "kubernetes", "secret", "security", ".github", "docker-compose",
)


@dataclass(frozen=True)
class WorkspaceMutationPolicyResult:
    status: str  # "ok" | "policy_violation"
    allowed_changes: list[str] = field(default_factory=list)
    blocked_changes: list[dict[str, str]] = field(default_factory=list)
    questionable_changes: list[dict[str, str]] = field(default_factory=list)
    escalate_to_strict: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def acceptable(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta_workspace_mutation_policy_result.v1",
            "status": self.status,
            "allowed_changes": list(self.allowed_changes),
            "blocked_changes": list(self.blocked_changes),
            "questionable_changes": list(self.questionable_changes),
            "escalate_to_strict": self.escalate_to_strict,
            "warnings": list(self.warnings),
        }


class AnantaWorkspaceMutationPolicyService:
    """Resolves mutation modes and validates changed workspace files."""

    def resolve_mutation_mode(
        self,
        *,
        cfg: dict[str, Any],
        task_kind: str | None = None,
        risk: str | None = None,
        explicit_mode: str | None = None,
    ) -> str:
        """AWWPI-002/013: explicit > task_kind mapping > read_only fallback.

        Unknown modes fall back to read_only; high/critical risk escalates
        controlled_workspace to strict_patch_request.
        """
        mode = str(explicit_mode or cfg.get("mutation_mode") or "").strip().lower()
        if mode not in VALID_MUTATION_MODES:
            mode = ""
        if not mode:
            mapping = cfg.get("mode_by_task_kind") if isinstance(cfg.get("mode_by_task_kind"), dict) else {}
            mapped = str(mapping.get(str(task_kind or "").strip().lower()) or "").strip().lower()
            mode = mapped if mapped in VALID_MUTATION_MODES else MUTATION_MODE_READ_ONLY
        escalate_risks = {str(item or "").strip().lower() for item in list(cfg.get("escalate_to_strict_risks") or ["high", "critical"])}
        if mode == MUTATION_MODE_CONTROLLED_WORKSPACE and str(risk or "").strip().lower() in escalate_risks:
            return MUTATION_MODE_STRICT_PATCH_REQUEST
        return mode

    @staticmethod
    def _is_forbidden(rel: str) -> str | None:
        parts = [part for part in Path(rel).parts if part]
        if not parts:
            return "empty_path"
        if Path(rel).is_absolute() or rel.startswith("~"):
            return "absolute_path_blocked"
        if any(part == ".." for part in parts):
            return "path_traversal_blocked"
        if any(part in _ALWAYS_FORBIDDEN_SEGMENTS for part in parts):
            return f"forbidden_segment:{next(part for part in parts if part in _ALWAYS_FORBIDDEN_SEGMENTS)}"
        name = parts[-1].lower()
        if any(fnmatch.fnmatch(name, pattern) for pattern in _SECRET_NAME_PATTERNS):
            return "secret_like_file_blocked"
        return None

    def is_strict_required_path(self, rel: str, *, markers: list[str] | None = None) -> bool:
        normalized = str(rel or "").replace("\\", "/").lower()
        effective = [str(item or "").strip().lower() for item in (markers or list(_DEFAULT_STRICT_PATH_MARKERS)) if str(item or "").strip()]
        return any(marker in normalized for marker in effective)

    def evaluate_changed_files(
        self,
        *,
        workspace_dir: Path | str,
        changed_rel_paths: list[str] | None,
        materialization_manifest: Any = None,
        allowed_new_file_globs: list[str] | None = None,
        require_materialized_scope: bool = True,
        strict_path_markers: list[str] | None = None,
        domain_allowed_write_paths: list[str] | None = None,
    ) -> WorkspaceMutationPolicyResult:
        """AWWPI-008: validate every meaningful changed file against scope.

        A change is allowed when the file is materialized with a write/patch
        operation in the manifest, or matches ``allowed_new_file_globs``.
        Without a manifest and with ``require_materialized_scope`` off, the
        change is flagged questionable but not blocked.

        CCRDS-011: ``domain_allowed_write_paths`` is the runtime-domain-scope
        hook — when set (active scope), every change must additionally lie
        under one of these repo-relative path prefixes; everything else is
        blocked as ``outside_domain_write_scope``.
        """
        workspace = Path(workspace_dir).resolve()
        allowed: list[str] = []
        blocked: list[dict[str, str]] = []
        questionable: list[dict[str, str]] = []
        warnings: list[str] = []
        escalate = False

        manifest_rows = materialization_manifest if isinstance(materialization_manifest, list) else []
        writable_paths: set[str] = set()
        for row in manifest_rows:
            if not isinstance(row, dict):
                continue
            rel = str(row.get("workspace_path") or row.get("workspace_relative_path") or "").replace("\\", "/").strip()
            ops = {str(item or "").strip().lower() for item in list(row.get("allowed_operations") or [])}
            if rel and ({"write", "patch", "replace"} & ops or not ops):
                writable_paths.add(rel)

        new_globs = [str(item or "").strip() for item in list(allowed_new_file_globs or []) if str(item or "").strip()]

        for raw in list(changed_rel_paths or []):
            rel = str(raw or "").strip().replace("\\", "/")
            if not rel:
                continue
            forbidden_reason = self._is_forbidden(rel)
            if forbidden_reason:
                blocked.append({"path": rel, "reason": forbidden_reason})
                continue
            resolved = (workspace / rel).resolve()
            try:
                inside = str(resolved).startswith(str(workspace))
            except OSError:
                inside = False
            if not inside:
                blocked.append({"path": rel, "reason": "outside_workspace_root"})
                continue
            deleted = not resolved.exists()
            if deleted:
                blocked.append({"path": rel, "reason": "delete_or_rename_requires_separate_approval"})
                continue
            if domain_allowed_write_paths is not None:
                from agent.codecompass.domain_scope import is_path_within
                if not is_path_within(rel, domain_allowed_write_paths):
                    blocked.append({"path": rel, "reason": "outside_domain_write_scope"})
                    continue
            if self.is_strict_required_path(rel, markers=strict_path_markers):
                escalate = True
            if rel in writable_paths or any(fnmatch.fnmatch(rel, glob) for glob in new_globs):
                allowed.append(rel)
                continue
            if manifest_rows or require_materialized_scope:
                blocked.append({"path": rel, "reason": "not_in_materialized_scope"})
            else:
                questionable.append({"path": rel, "reason": "no_materialization_manifest"})

        if not manifest_rows:
            warnings.append("no_materialization_manifest")
        if escalate:
            warnings.append("strict_path_marker_matched")
        status = "policy_violation" if blocked else "ok"
        return WorkspaceMutationPolicyResult(
            status=status,
            allowed_changes=sorted(allowed),
            blocked_changes=sorted(blocked, key=lambda row: row["path"]),
            questionable_changes=sorted(questionable, key=lambda row: row["path"]),
            escalate_to_strict=escalate,
            warnings=warnings,
        )


ananta_workspace_mutation_policy_service = AnantaWorkspaceMutationPolicyService()


def get_ananta_workspace_mutation_policy_service() -> AnantaWorkspaceMutationPolicyService:
    return ananta_workspace_mutation_policy_service
