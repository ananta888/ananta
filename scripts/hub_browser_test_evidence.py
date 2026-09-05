"""Hub-side reservation boundary for headless browser test evidence."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import http.server
import json
import os
import platform
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def repository_revision(root: Path) -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip().lower()


def source_digest(root: Path, paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        resolved = (root / relative).resolve(strict=True)
        resolved.relative_to(root.resolve())
        if not resolved.is_file():
            raise ValueError("browser_evidence_source_invalid")
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(resolved.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def command_version(command: Sequence[str], *, cwd: Path) -> str:
    return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def host_environment(*, frontend: Path) -> dict[str, Any]:
    cpu_model = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cpu_model = next(
            (
                line.split(":", 1)[1].strip()
                for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.startswith("model name") and ":" in line
            ),
            "unknown",
        )
    pages = os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else 0
    page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 0
    return {
        "platform": platform.platform(),
        "cpu_model": cpu_model,
        "logical_cpu_count": os.cpu_count(),
        "memory_bytes": pages * page_size,
        "python": platform.python_version(),
        "node": command_version(("node", "--version"), cwd=frontend),
        "playwright": command_version(("npx", "playwright", "--version"), cwd=frontend),
    }


@contextlib.contextmanager
def localhost_origin() -> Iterator[str]:
    """Serve an empty trustworthy localhost origin for browser capability gates."""

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

    with tempfile.TemporaryDirectory(prefix="ananta-browser-origin-") as directory:
        handler = functools.partial(QuietHandler, directory=directory)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@dataclass(frozen=True, slots=True)
class HubBrowserTestRun:
    """Closed Hub reservation handed to exactly one browser subprocess."""

    registry: HubEvidenceRegistryService
    tenant_id: str
    project_id: str
    task_id: str
    assignment_id: str
    dispatch_lease_id: str
    repository_revision: str
    source_id: str
    run_id: str
    binding_digest: str
    assignment: Mapping[str, Any]

    @classmethod
    def reserve(
        cls,
        *,
        root: Path,
        registry_db: Path,
        task_id: str,
        source_paths: Sequence[Path],
        execution_profile: Mapping[str, Any],
        environment: Mapping[str, Any],
        tenant_id: str = "ananta-local",
        project_id: str = "ananta",
    ) -> HubBrowserTestRun:
        revision = repository_revision(root)
        inputs = source_digest(root, source_paths)
        policy = source_digest(
            root,
            (Path("AGENTS.md"), Path("docs/decisions/ADR-decentralized-peer-overlay.md")),
        )
        registry_db.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{registry_db}")
        SQLModel.metadata.create_all(
            engine,
            tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
        )
        registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
        source = registry.register_source(
            tenant_id=tenant_id,
            project_id=project_id,
            origin_type="test_harness",
            origin_digest=canonical_digest([path.as_posix() for path in source_paths]),
            content_digest=inputs,
            policy_digest=policy,
            evidence_scope="test",
            synthetic=True,
        )
        nonce = uuid.uuid4().hex
        assignment_id = f"assignment-{nonce}"
        dispatch_lease_id = f"lease-{nonce}"
        reserved = registry.reserve_run(
            tenant_id=tenant_id,
            project_id=project_id,
            task_id=task_id,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
            repository_revision=revision,
            input_digest=inputs,
            execution_profile_digest=canonical_digest(execution_profile),
            environment_digest=canonical_digest(environment),
            source_ids=[source.source_id],
            evidence_scope="test",
            idempotency_key=f"browser-gate-{task_id.lower()}-{revision[:12]}-{nonce}",
            synthetic=True,
        )
        assignment = registry.assignment_projection(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=reserved.run_id,
            task_id=task_id,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
        )
        return cls(
            registry=registry,
            tenant_id=tenant_id,
            project_id=project_id,
            task_id=task_id,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
            repository_revision=revision,
            source_id=source.source_id,
            run_id=reserved.run_id,
            binding_digest=reserved.binding_digest,
            assignment=assignment,
        )

    def complete(self, result_payload: Mapping[str, Any], *, succeeded: bool) -> dict[str, Any]:
        result_digest = canonical_digest(result_payload)
        recorded = self.registry.record_result(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            run_id=self.run_id,
            assignment_id=self.assignment_id,
            dispatch_lease_id=self.dispatch_lease_id,
            terminal_state="succeeded" if succeeded else "failed",
            result_digest=result_digest,
        )
        release = self.registry.verify_release_binding(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            run_id=recorded.run_id,
            required_scope="local",
            task_id=self.task_id,
            repository_revision=self.repository_revision,
            source_ids=[self.source_id],
        )
        return {
            "issuer": recorded.issuer,
            "source_id": self.source_id,
            "run_id": recorded.run_id,
            "scope": recorded.evidence_scope,
            "synthetic": recorded.synthetic,
            "binding_digest": recorded.binding_digest,
            "result_digest": result_digest,
            "production_release_eligible": release.verified,
            "production_release_reason": release.reason_code,
        }


__all__ = [
    "HubBrowserTestRun",
    "canonical_digest",
    "command_version",
    "host_environment",
    "localhost_origin",
    "repository_revision",
    "source_digest",
]
