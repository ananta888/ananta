#!/usr/bin/env python3
"""Exercise the real GitHub OAuth source path under Hub-issued evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, create_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.db_models.evidence_identity import (  # noqa: E402
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.repositories.evidence_identity import (  # noqa: E402
    SqlEvidenceIdentityRepository,
)
from agent.services.git_remote_policy_service import (  # noqa: E402
    GitRemoteAccessPolicy,
)
from agent.services.hub_evidence_gate_service import (  # noqa: E402
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
    canonical_evidence_digest,
)
from agent.services.hub_evidence_registry_service import (  # noqa: E402
    HubEvidenceRegistryService,
)
from agent.services.hub_git_authorization_provisioning import (  # noqa: E402
    GitAuthorizationProvisioningRequest,
    GitAuthorizationSelection,
)
from agent.services.hub_git_authorization_registry import (  # noqa: E402
    RegisteredGitAuthorization,
    ScopedGitAuthorizationRegistry,
)
from agent.services.hub_git_credential_resolver import (  # noqa: E402
    SubprocessGitCredentialCommandResolver,
)
from agent.services.hub_git_github_authorization_provider import (  # noqa: E402
    GitHubAppInstallationSecretResolver,
    GitHubAuthorizationProvisioner,
    HttpGitHubAuthorizationApi,
)
from agent.services.hub_git_transport import HubGitTransport  # noqa: E402
from agent.sources.git_source_connector_common import GitSourceScope  # noqa: E402
from agent.sources.github_repository_connector import (  # noqa: E402
    GitHubRepositoryConnector,
)
from agent.sources.hub_git_connector_providers import (  # noqa: E402
    HubGitContentProvider,
    HubGitHubCommitResolver,
    HubGitHubRepositoryEndpointProvider,
)
from agent.sources.source_connectors import (  # noqa: E402
    ConnectorRefreshRequest,
    SourceConnectorError,
)

TASK_ID = "SRCCTRL-GITHUB-OAUTH-LIVE-GATE"
HANDLE = "github-oauth:live-gate"
SOURCE_PATHS = (
    "agent/repositories/hub_git_authorization_repository.py",
    "agent/services/git_remote_policy_service.py",
    "agent/services/hub_evidence_gate_service.py",
    "agent/services/hub_evidence_registry_service.py",
    "agent/services/hub_git_authorization_provisioning.py",
    "agent/services/hub_git_authorization_registry.py",
    "agent/services/hub_git_credential_resolver.py",
    "agent/services/hub_git_github_authorization_provider.py",
    "agent/services/hub_git_transport.py",
    "agent/sources/git_source_connector_common.py",
    "agent/sources/github_repository_connector.py",
    "agent/sources/hub_git_connector_providers.py",
    "scripts/run_hub_evidence_github_oauth_gate.py",
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class GitHubOAuthEvidenceGateError(ValueError):
    """Bounded live-gate configuration or execution failure."""


@dataclass(repr=False)
class _MemoryOAuthGrantStore:
    token: str

    def resolve_token(self, handle: str) -> str:
        if handle != HANDLE:
            raise GitHubOAuthEvidenceGateError("github_oauth_gate_grant_unknown")
        return self.token

    def __repr__(self) -> str:
        return "_MemoryOAuthGrantStore(token=<redacted>)"


class _UnusedAppJwtIssuer:
    def issue(self) -> str:
        raise GitHubOAuthEvidenceGateError("github_oauth_gate_app_path_forbidden")


@dataclass(frozen=True)
class _ActiveCredentialStatus:
    reference: str

    def status(self, credential_ref: str) -> str:
        return "active" if credential_ref == self.reference else "unavailable"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(root: Path = ROOT) -> dict[str, Any]:
    entries = [
        {
            "path": path,
            "sha256": sha256_file((root / path).resolve(strict=True)),
        }
        for path in SOURCE_PATHS
    ]
    return {"entries": entries, "digest": canonical_evidence_digest(entries)}


def repository_revision(root: Path = ROOT) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    revision = completed.stdout.strip().lower()
    if completed.returncode != 0 or _SHA.fullmatch(revision) is None:
        raise GitHubOAuthEvidenceGateError(
            "github_oauth_gate_repository_revision_invalid"
        )
    changed = subprocess.run(
        ("git", "diff", "--quiet", "HEAD", "--", *SOURCE_PATHS),
        cwd=root,
        check=False,
    )
    if changed.returncode != 0:
        raise GitHubOAuthEvidenceGateError(
            "github_oauth_gate_bound_sources_dirty"
        )
    return revision


def read_oauth_token() -> str:
    if sys.stdin.isatty():
        raise GitHubOAuthEvidenceGateError(
            "github_oauth_gate_token_stdin_required"
        )
    token = sys.stdin.read(4097).strip()
    if (
        not token
        or len(token) > 4096
        or any(character.isspace() or not character.isprintable() for character in token)
    ):
        raise GitHubOAuthEvidenceGateError("github_oauth_gate_token_invalid")
    return token


def oauth_scope_assessment(
    scopes: frozenset[str], *, visibility: str
) -> dict[str, Any]:
    normalized = frozenset(str(scope).strip() for scope in scopes if str(scope).strip())
    accepted = frozenset({"repo"} if visibility == "private" else {"public_repo"})
    required_present = bool(normalized & {"repo", "public_repo", "contents:read"})
    return {
        "observed": sorted(normalized),
        "required_repository_scope_present": required_present,
        "minimal_for_visibility": required_present and normalized.issubset(accepted),
    }


def projection_passed(
    execution: Mapping[str, Any], *, expected_immutable_ref: str
) -> bool:
    return bool(
        execution.get("refresh_status") == "ok"
        and execution.get("immutable_ref") == expected_immutable_ref
        and isinstance(execution.get("item_count"), int)
        and int(execution["item_count"]) > 0
        and isinstance(execution.get("total_bytes"), int)
        and int(execution["total_bytes"]) > 0
        and _DIGEST.fullmatch(str(execution.get("manifest_digest") or ""))
        is not None
        and execution.get("credential_ref_opaque") is True
        and execution.get("token_exposed") is False
        and execution.get("revoked_health") == "authorization_required"
        and execution.get("revoked_index_reason") == "authorization_required"
    )


def _git_version() -> str:
    completed = subprocess.run(
        ("git", "--version"), capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise GitHubOAuthEvidenceGateError("github_oauth_gate_git_unavailable")
    return completed.stdout.strip()[:160]


def _prepare_database(database_url: str) -> str:
    parsed = make_url(database_url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in {
        None,
        "",
        ":memory:",
    }:
        Path(parsed.database).expanduser().resolve().parent.mkdir(
            parents=True, exist_ok=True
        )
    return database_url


def execute_gate(
    *,
    token: str,
    repository: str,
    requested_ref: str,
    output_path: Path,
    database_url: str,
) -> tuple[dict[str, Any], int]:
    revision = repository_revision()
    manifest = source_manifest()
    scope = GitSourceScope(
        tenant_id="ananta-external",
        project_id="source-control-github",
        owner_id="github-live-gate",
    )
    api = HttpGitHubAuthorizationApi()
    grants = _MemoryOAuthGrantStore(token)
    selection = GitAuthorizationSelection(
        authorization_handle=HANDLE,
        authorization_kind="github_oauth",
        repository=repository,
    )
    provider = GitHubAuthorizationProvisioner(api=api, oauth_grants=grants)
    resolved = provider.resolve_authorization(
        GitAuthorizationProvisioningRequest(scope=scope, selection=selection)
    )
    metadata = api.inspect_repository(repository=repository, access_token=token)
    visibility = str(metadata.get("visibility") or "unknown").strip().lower()
    observed_scopes = api.inspect_oauth_scopes(access_token=token)
    scope_assessment = oauth_scope_assessment(
        observed_scopes, visibility=visibility
    )
    record = RegisteredGitAuthorization(
        scope=scope,
        connection_ref=resolved.connection_ref,
        authorization_kind=resolved.authorization_kind,
        remote_url=resolved.remote_url,
        credential_ref=resolved.credential_ref,
        credential_username=resolved.credential_username,
        authorization_state=resolved.authorization_state,
        granted_scopes=resolved.granted_scopes,
        repository=resolved.repository,
    )
    registry = ScopedGitAuthorizationRegistry([record])
    policy = GitRemoteAccessPolicy(
        credential_status=_ActiveCredentialStatus(str(record.credential_ref))
    )
    secret_resolver = GitHubAppInstallationSecretResolver(
        api=api,
        jwt_issuer=_UnusedAppJwtIssuer(),
        oauth_grants=grants,
    )

    with tempfile.TemporaryDirectory(prefix="ananta-github-oauth-gate-") as temporary:
        temporary_root = Path(temporary)
        command_resolver = SubprocessGitCredentialCommandResolver(
            secret_resolver=secret_resolver,
            credential_root=temporary_root / "credentials",
        )
        transport = HubGitTransport(
            credential_resolver=command_resolver,
            workspace_root=temporary_root / "workspaces",
        )
        content = HubGitContentProvider(registry=registry, transport=transport)
        connector = GitHubRepositoryConnector(
            endpoint_provider=HubGitHubRepositoryEndpointProvider(
                registry=registry
            ),
            commit_resolver=HubGitHubCommitResolver(
                registry=registry, transport=transport
            ),
            remote_policy=policy,
            inventory_provider=content,
            refresh_provider=content,
        )
        descriptor = {
            "source_id": "github-oauth-live-source",
            "source_type": "github_repository",
            "tenant_id": scope.tenant_id,
            "project_id": scope.project_id,
            "owner_id": scope.owner_id,
            "github_authorization_ref": HANDLE,
            "repository": repository,
            "ref": requested_ref,
        }
        resolved_revision = connector.resolve_revision(descriptor)
        immutable_ref = resolved_revision.immutable_ref
        remote_commit = immutable_ref.removeprefix("git-commit:")
        if _SHA.fullmatch(remote_commit) is None:
            raise GitHubOAuthEvidenceGateError(
                "github_oauth_gate_remote_revision_invalid"
            )

        environment = {
            "schema": "ananta.github-oauth-live-environment.v1",
            "python": platform.python_version(),
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "git": _git_version(),
            "provider": "github.com",
            "repository_visibility": visibility,
        }
        execution_profile = {
            "schema": "ananta.github-oauth-live-profile.v1",
            "repository": repository,
            "requested_ref": requested_ref,
            "expected_immutable_ref": immutable_ref,
            "submodules": "disabled",
            "lfs": "disabled",
            "oauth_scope_assessment": scope_assessment,
        }
        nonce = uuid.uuid4().hex
        engine = create_engine(_prepare_database(database_url))
        SQLModel.metadata.create_all(
            engine,
            tables=[
                HubSourceEvidenceIdentityDB.__table__,
                HubRunEvidenceIdentityDB.__table__,
            ],
        )
        evidence_registry = HubEvidenceRegistryService(
            SqlEvidenceIdentityRepository(engine)
        )
        policy_digest = canonical_evidence_digest(
            {
                "authorization": "github_oauth",
                "required_scope": "contents:read",
                "redirects": "deny",
                "submodules": "deny",
                "lfs_payloads": "deny",
                "revoke": "fail_closed",
            }
        )
        request = EvidenceGateRequest(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            task_id=TASK_ID,
            assignment_id=f"github-oauth-assignment-{nonce}",
            dispatch_lease_id=f"github-oauth-lease-{nonce}",
            repository_revision=revision,
            input_digest=canonical_evidence_digest(
                {
                    "source_manifest": manifest["digest"],
                    "remote_commit": remote_commit,
                    "repository": repository,
                }
            ),
            execution_profile_digest=canonical_evidence_digest(
                execution_profile
            ),
            environment_digest=canonical_evidence_digest(environment),
            evidence_scope="external",
            required_scope="external",
            idempotency_key=f"github-oauth:{revision}:{remote_commit}:{nonce}",
            sources=(
                EvidenceGateSourceAdmission(
                    "repository_bundle",
                    manifest["digest"],
                    manifest["digest"],
                    policy_digest,
                ),
                EvidenceGateSourceAdmission(
                    "github_repository",
                    canonical_evidence_digest(
                        {
                            "provider": "github.com",
                            "repository": repository,
                            "requested_ref": requested_ref,
                        }
                    ),
                    canonical_evidence_digest({"commit_sha": remote_commit}),
                    policy_digest,
                ),
            ),
        )

        def worker(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
            refreshed = connector.refresh(
                descriptor, ConnectorRefreshRequest(dry_run=False)
            )
            registry.set_authorization_state(
                scope=scope,
                connection_ref=HANDLE,
                repository=repository,
                authorization_state="revoked",
            )
            health = connector.health(descriptor)
            revoked_reason = None
            try:
                connector.assert_indexable(descriptor)
            except SourceConnectorError as exc:
                revoked_reason = exc.reason_code
            execution = {
                "passed": False,
                "reason_code": "github_oauth_live_gate_failed",
                "assignment_bound": (
                    assignment.get("task_id") == TASK_ID
                    and assignment.get("evidence_scope") == "external"
                    and len(assignment.get("source_ids") or ()) == 2
                    and re.fullmatch(
                        r"RUN_[0-9a-f]{32}",
                        str(assignment.get("run_id") or ""),
                    )
                    is not None
                    and _DIGEST.fullmatch(
                        str(assignment.get("binding_digest") or "")
                    )
                    is not None
                ),
                "authorization_kind": record.authorization_kind,
                "granted_scopes": sorted(record.granted_scopes),
                "oauth_scope_assessment": scope_assessment,
                "repository": repository,
                "repository_visibility": visibility,
                "requested_ref": requested_ref,
                "immutable_ref": refreshed.get("immutable_ref"),
                "revision_digest": refreshed.get("revision_digest"),
                "refresh_status": refreshed.get("status"),
                "manifest_digest": refreshed.get("manifest_digest"),
                "item_count": refreshed.get("item_count"),
                "total_bytes": refreshed.get("total_bytes"),
                "exclusions": list(refreshed.get("exclusions") or []),
                "credential_ref_opaque": str(record.credential_ref).startswith(
                    "secret://github-oauth/grant/"
                ),
                "revoked_health": health.status,
                "revoked_index_reason": revoked_reason,
                "token_exposed": False,
            }
            encoded = json.dumps(execution, sort_keys=True)
            execution["token_exposed"] = token in encoded
            execution["passed"] = bool(
                execution["assignment_bound"]
                and projection_passed(
                    execution, expected_immutable_ref=immutable_ref
                )
            )
            execution["reason_code"] = (
                "github_oauth_live_gate_passed"
                if execution["passed"]
                else "github_oauth_live_gate_failed"
            )
            return execution

        outcome = HubEvidenceGateService(evidence_registry).execute(
            request, worker
        )

    report = {
        "schema": "ananta.hub-evidence-github-oauth-gate-result.v1",
        "status": "passed" if outcome.passed and outcome.verified else "failed",
        "reason_code": outcome.reason_code,
        "repository_revision": revision,
        "source_ids": list(outcome.source_ids),
        "run_id": outcome.run_id,
        "result_digest": outcome.result_digest,
        "evidence_scope": "external",
        "verified": outcome.verified,
        "execution_profile": execution_profile,
        "environment": environment,
        "execution": dict(outcome.execution),
        "human_intervention_required": False,
        "production_release_eligible": False,
    }
    encoded_report = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if token in encoded_report:
        raise GitHubOAuthEvidenceGateError("github_oauth_gate_token_exposed")
    output_path.resolve().parent.mkdir(parents=True, exist_ok=True)
    output_path.resolve().write_text(encoded_report, encoding="utf-8")
    return report, 0 if report["status"] == "passed" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="ananta888/ananta")
    parser.add_argument("--ref", default="main")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/github-oauth-live-evidence.json",
    )
    parser.add_argument(
        "--database-url",
        default=f"sqlite:///{ROOT / 'data/hub-evidence-github-oauth.sqlite3'}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, returncode = execute_gate(
        token=read_oauth_token(),
        repository=args.repository,
        requested_ref=args.ref,
        output_path=args.output,
        database_url=args.database_url,
    )
    print(
        json.dumps(
            {"status": report["status"], "run_id": report["run_id"]},
            sort_keys=True,
        )
    )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
