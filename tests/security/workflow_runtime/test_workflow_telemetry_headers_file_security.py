from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.services.workflow_runtime.telemetry import WorkflowTelemetryError
from agent.services.workflow_runtime.telemetry_runtime import _read_headers_file


def _configure(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("ANANTA_WORKFLOW_OTEL_HEADERS_FILE", str(path))


def _write_headers(path: Path, *, mode: int = 0o600) -> Path:
    path.write_text(
        '{"Authorization":"opaque-collector-credential"}',
        encoding="utf-8",
    )
    path.chmod(mode)
    return path


def test_otel_headers_reader_preserves_absolute_path_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ANANTA_WORKFLOW_OTEL_HEADERS_FILE",
        "relative/collector-headers.json",
    )

    with pytest.raises(
        WorkflowTelemetryError,
        match="^opentelemetry_headers_file_must_be_absolute$",
    ):
        _read_headers_file()


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="platform has no O_NOFOLLOW")
def test_otel_headers_reader_rejects_symlink_without_disclosing_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _write_headers(tmp_path / "collector-headers.json")
    configured = tmp_path / "configured-headers.json"
    configured.symlink_to(target)
    _configure(monkeypatch, configured)

    with pytest.raises(WorkflowTelemetryError) as raised:
        _read_headers_file()

    assert str(raised.value) == "opentelemetry_headers_file_unreadable"
    assert "opaque-collector-credential" not in str(raised.value)
    assert str(configured) not in str(raised.value)


def test_otel_headers_reader_rejects_hardlinked_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = _write_headers(tmp_path / "configured-headers.json")
    (tmp_path / "second-link.json").hardlink_to(configured)
    _configure(monkeypatch, configured)

    with pytest.raises(
        WorkflowTelemetryError,
        match="^opentelemetry_headers_file_invalid$",
    ):
        _read_headers_file()


@pytest.mark.parametrize("mode", (0o620, 0o602), ids=("group-writable", "world-writable"))
def test_otel_headers_reader_rejects_writable_group_or_world_permissions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: int,
) -> None:
    configured = _write_headers(tmp_path / "configured-headers.json", mode=mode)
    _configure(monkeypatch, configured)

    with pytest.raises(
        WorkflowTelemetryError,
        match="^opentelemetry_headers_file_invalid$",
    ):
        _read_headers_file()


def test_otel_headers_reader_rejects_oversized_file_before_json_decode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured-headers.json"
    configured.write_bytes(b"{" + b"x" * 32_768 + b"}")
    configured.chmod(0o600)
    _configure(monkeypatch, configured)

    with pytest.raises(
        WorkflowTelemetryError,
        match="^opentelemetry_headers_file_invalid$",
    ):
        _read_headers_file()


@pytest.mark.parametrize(
    "payload",
    (
        b"not-json",
        b"[]",
        b'{"Authorization":42}',
        b'{"":"opaque-collector-credential"}',
    ),
    ids=("malformed", "not-object", "non-string-value", "blank-header-name"),
)
def test_otel_headers_reader_rejects_invalid_json_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: bytes,
) -> None:
    configured = tmp_path / "configured-headers.json"
    configured.write_bytes(payload)
    configured.chmod(0o600)
    _configure(monkeypatch, configured)

    with pytest.raises(
        WorkflowTelemetryError,
        match="^opentelemetry_headers_file_invalid$",
    ):
        _read_headers_file()
