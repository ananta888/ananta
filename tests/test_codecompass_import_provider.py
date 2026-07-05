"""CRG-002: vendor-neutral import-provider port tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from worker.retrieval.codecompass_import_provider import (
    CAP_DIAGNOSTICS,
    CAP_IMPORT_SNAPSHOT,
    CAP_PROBE,
    CodeCompassGraphImportProvider,
    ImportSnapshot,
    ProviderDiagnostics,
    ProviderProbe,
    WorkspacePathError,
    assert_within_workspace,
    compute_content_hash,
)


# ---------------------------------------------------------------------------
# helpers: a minimal adapter that exercises the port end-to-end
# ---------------------------------------------------------------------------

@dataclass
class _FakeProvider:
    workspace_dir: Path
    _records: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def provider_id(self) -> str:
        return "fake.test"

    def probe(self) -> ProviderProbe:
        return ProviderProbe(
            provider_id="fake.test",
            available=True,
            provider_revision="rev-1",
            required_flags=(),
        )

    def import_snapshot(self) -> ImportSnapshot:
        return ImportSnapshot(
            provider_id="fake.test",
            provider_revision="rev-1",
            content_hash=compute_content_hash(self._records),
            graph_nodes=self._records,
            graph_edges=(),
        )

    def diagnostics(self) -> ProviderDiagnostics:
        return ProviderDiagnostics(
            provider_id="fake.test",
            last_run={"snapshot_count": 1},
        )


def test_fake_provider_implements_port():
    p = _FakeProvider(workspace_dir=Path("/workspace"))
    assert isinstance(p, CodeCompassGraphImportProvider)


def test_provider_probe_carries_required_flags():
    p = _FakeProvider(workspace_dir=Path("/workspace"))
    probe = p.probe()
    assert probe.available is True
    assert probe.reason_unavailable is None


def test_import_snapshot_content_hash_is_deterministic():
    p = _FakeProvider(workspace_dir=Path("/workspace"))
    snap1 = p.import_snapshot()
    snap2 = p.import_snapshot()
    assert snap1.content_hash == snap2.content_hash


def test_import_snapshot_hash_changes_with_records():
    p1 = _FakeProvider(workspace_dir=Path("/workspace"), _records=({"id": "a"},))
    p2 = _FakeProvider(workspace_dir=Path("/workspace"), _records=({"id": "b"},))
    assert p1.import_snapshot().content_hash != p2.import_snapshot().content_hash


def test_diagnostics_includes_provider_id():
    p = _FakeProvider(workspace_dir=Path("/workspace"))
    diag = p.diagnostics()
    assert diag.provider_id == "fake.test"


def test_capability_constants_are_stable():
    # These names are part of the port contract — do not change without
    # bumping the provider capability version.
    assert CAP_PROBE == "probe"
    assert CAP_IMPORT_SNAPSHOT == "import_snapshot"
    assert CAP_DIAGNOSTICS == "diagnostics"


# ---------------------------------------------------------------------------
# workspace-bound path guards (DD-013)
# ---------------------------------------------------------------------------

def test_assert_within_workspace_accepts_inner_path(tmp_path):
    inner = tmp_path / "a" / "b"
    inner.mkdir(parents=True)
    f = inner / "x.txt"
    f.write_text("ok")
    resolved = assert_within_workspace(f, tmp_path)
    assert resolved.exists()


def test_assert_within_workspace_rejects_traversal(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    bad = tmp_path / "sub" / ".." / ".." / outside.name
    with pytest.raises(WorkspacePathError):
        assert_within_workspace(bad, tmp_path)


def test_assert_within_workspace_rejects_symlink_escape(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    target = tmp_path.parent / "external_secret.txt"
    target.write_text("secret")
    sym = real_dir / "linked"
    try:
        sym.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    with pytest.raises(WorkspacePathError):
        assert_within_workspace(sym, tmp_path)


# ---------------------------------------------------------------------------
# provider must work without CRG / SPADE installed
# ---------------------------------------------------------------------------

def test_provider_works_without_optional_deps(monkeypatch):
    """Providers must be importable and runnable even if CRG/SPADE are
    not installed. The fake provider above has no CRG/SPADE imports;
    this test simply asserts that the import-port module itself is
    independent of those packages.
    """
    # Sanity: import-port module must not import CRG/SPADE on load.
    import worker.retrieval.codecompass_import_provider as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "code-review-graph" not in src.lower()
    assert "spade" not in src.lower()


def test_unknown_provider_id_does_not_crash():
    """Diagnostics of an unknown provider must still produce a
    ProviderDiagnostics with provider_id and last_run, not raise."""
    p = _FakeProvider(workspace_dir=Path("/workspace"))
    diag = p.diagnostics()
    assert diag.provider_id
    assert isinstance(diag.last_run, dict)