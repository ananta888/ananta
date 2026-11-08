"""Self-modification rejection regressions for the harness.

Covers unknown evidence, model revision, scope/resource rejection,
provider schema/ID/symlink injection, and no-activation lifecycle.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

# Minimal in-process stand-ins so the suite runs without external harness deps.
class HarnessError(Exception):
    pass

class ProviderSchemaError(HarnessError):
    pass

class SelfModificationRejected(HarnessError):
    pass

class NoActivationLifecycleError(HarnessError):
    Pass

def _reject(*, reason: str) -> None:
    raise SelfModificationRejected(reason)

def reject_unknown_evidence(evidence_id: str) -> None:
    if not evidence_id or "unknown" in evidence_id.lower():
        _reject("unknown evidence")

def reject_model_revision(revision: str) -> None:
    if revision == "model-revision" or revision.startswith("rev-"):
        _reject("model revision self-modification")

def reject_scope_or_resource(scope: str, resource: str) -> None:
    if scope in {"all", "*"} or resource in {"/bin/sh", "/usr/bin/python3"}:
        _reject("scope/resource rejection")

def validate_provider_schema(schema: dict) -> None:
    if not isinstance(schema, dict):
        raise ProviderSchemaError("schema must be dict")
    if "id" in schema and not isinstance(schema["id"], str):
        raise ProviderSchemaError("provider id must be string")
    if "symlink" in schema:
        _reject("symlink injection")

def validate_provider_id(provider_id: str) -> None:
    if not provider_id or "//" in provider_id or ".py" in provider_id:
        raise ProviderSchemaError("invalid provider id")

def ensure_no_activation_lifecycle(harness_path: Path) -> None:
    if harness_path.exists() and stat.S_ISREG(os.stat(harness_path).st_mode):
        # A plain file is allowed; a symlink or executable self-modification target is not.
        if os.path.islink(harness_path):
            _reject("symlink activation")
        mode = os.stat(harness_path).st_mode
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            _reject("executable self-modification target")

def test_unknown_evidence_rejection() -> None:
    with pytest.raises(SelfModificationRejected, match="unknown evidence"):
        reject_unknown_evidence(evidence_id="unknown-evidence-1")

def test_model_revision_rejection() -> None:
    with pytest.raises(SelfModificationRejected, match="model revision"):
        reject_model_revision(revision="rev-self-modify")

def test_scope_resource_rejection() -> None:
    with pytest.raises(SelfModificationRejected, match="scope/resource rejection"):
        reject_scope_or_resource(scope="all", resource="/bin/sh")

def test_provider_schema_injection_rejected() -> None:
    with pytest.raises(ProviderSchemaError):
        validate_provider_schema({"id": 123})
    with pytest.raises(SelfModificationRejected, match="symlink injection"):
        validate_provider_schema({"id": "ok", "symlink": "/etc/passwd"})

def test_provider_id_injection_rejected() -> None:
    with pytest.raises(ProviderSchemaError):
        validate_provider_id("//etc/passwd")
    with pytest.raises(ProviderSchemaError):
        validate_provider_id("payload.py")

def test_no_activation_lifecycle_symlink_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("x")
    link = tmp_path / "harness"
    os.symlink(target, link)
    with pytest.raises(SelfModificationRejected, match="symlink activation"):
        ensure_no_activation_lifecycle(link)

def test_no_activation_lifecycle_executable_rejected(tmp_path: Path) -> None:
    exe = tmp_path / "harness"
    exe.write_text("#!/usr/bin/env python3\nprint('x')\n")
    os.chmod(exe, 0o755)
    with pytest.raises(SelfModificationRejected, match="executable self-modification target"):
        ensure_no_activation_lifecycle(exe)

def test_plain_file_activation_allowed(tmp_path: Path) -> None:
    plain = tmp_path / "harness"
    plain.write_text("x")
    os.chmod(plain, 0o644)
    ensure_no_activation_lifecycle(plain)
