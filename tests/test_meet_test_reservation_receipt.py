"""Reservation metadata is exclusive, durable and never includes dispatch authority."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.meet_test_reservation_receipt import write_reservation_receipt


def arguments():
    return {
        "reservation": SimpleNamespace(
            source_id="synthetic-source-not-evidence",
            run_id="synthetic-run-not-evidence",
            binding_digest="a" * 64,
            assignment={"capability": "PRIVATE-MARKER"},
            dispatch_lease_id="PRIVATE-MARKER",
        ),
        "source": {"revision": "b" * 40, "digest": "c" * 64, "private": "PRIVATE-MARKER"},
        "companion": {"revision": "d" * 40, "digest": "e" * 64, "private": "PRIVATE-MARKER"},
        "frontend_digest": "f" * 64,
        "profile": {
            "name": "test-only",
            "reference": "synthetic-reference",
            "timeout_seconds": 30,
            "environment": {"secret": "PRIVATE-MARKER"},
        },
    }


def test_closed_receipt_is_private_and_flushed_before_return(tmp_path, monkeypatch):
    sync = Mock(wraps=os.fsync)
    monkeypatch.setattr("scripts.meet_test_reservation_receipt.os.fsync", sync)
    path = tmp_path / "reservation.json"
    write_reservation_receipt(path, **arguments())
    value = json.loads(path.read_text())
    assert set(value) == {
        "schema",
        "state",
        "identity",
        "source",
        "companion",
        "frontend_digest",
        "profile",
        "execution_result_available",
        "production_release_eligible",
    }
    assert value["state"] == "reserved" and value["execution_result_available"] is False
    assert value["identity"]["synthetic"] is True and value["identity"]["scope"] == "test"
    assert value["production_release_eligible"] is False
    assert set(value["identity"]) == {"issuer", "source_id", "run_id", "binding_digest", "scope", "synthetic"}
    assert set(value["source"]) == set(value["companion"]) == {"revision", "digest"}
    assert "PRIVATE-MARKER" not in path.read_text()
    assert len(path.read_bytes()) <= 8192
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sync.call_count == (2 if hasattr(os, "O_DIRECTORY") else 1)


@pytest.mark.parametrize("kind", ["file", "link", "fifo"])
def test_existing_objects_are_never_overwritten_or_followed(tmp_path, kind):
    path, existing = tmp_path / "reservation.json", tmp_path / "existing"
    existing.write_text("unchanged")
    if kind == "file":
        path.write_text("unchanged")
    elif kind == "link":
        path.symlink_to(existing)
    else:
        os.mkfifo(path)
    with pytest.raises(FileExistsError):
        write_reservation_receipt(path, **arguments())
    assert existing.read_text() == "unchanged"
    if kind == "file":
        assert path.read_text() == "unchanged"
    elif kind == "link":
        assert path.is_symlink()
    else:
        assert stat.S_ISFIFO(path.stat().st_mode)


def test_oversized_or_missing_fields_fail_before_creating_file(tmp_path):
    path = tmp_path / "reservation.json"
    values = arguments()
    values["profile"]["reference"] = "x" * 8192
    with pytest.raises(ValueError, match="receipt_budget"):
        write_reservation_receipt(path, **values)
    assert not path.exists()
    del values["source"]["digest"]
    with pytest.raises(KeyError):
        write_reservation_receipt(path, **values)
    assert not path.exists()


def test_sync_failure_is_not_hidden_or_treated_as_completed_write(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.meet_test_reservation_receipt.os.fsync", Mock(side_effect=OSError("sync failed")))
    with pytest.raises(OSError, match="sync failed"):
        write_reservation_receipt(tmp_path / "reservation.json", **arguments())


def test_receipt_survives_abrupt_owned_process_exit_without_implying_a_test_result(tmp_path):
    source = """
import os, signal, sys
from pathlib import Path
from types import SimpleNamespace
from scripts.meet_test_reservation_receipt import write_reservation_receipt
reservation = SimpleNamespace(source_id='synthetic-source-not-evidence',
    run_id='synthetic-run-not-evidence', binding_digest='a' * 64)
snapshot = {'revision': 'b' * 40, 'digest': 'c' * 64}
write_reservation_receipt(Path(sys.argv[1]), reservation=reservation,
    source=snapshot, companion=snapshot, frontend_digest='d' * 64,
    profile={'name': 'synthetic', 'reference': 'synthetic', 'timeout_seconds': 1})
os.kill(os.getpid(), signal.SIGKILL)
"""
    path = tmp_path / "reservation.json"
    result = subprocess.run(
        [sys.executable, "-c", source, str(path)],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == -9
    receipt = json.loads(path.read_text())
    assert receipt["state"] == "reserved" and receipt["execution_result_available"] is False
    assert receipt["production_release_eligible"] is False
    assert not (tmp_path / "report.json").exists()
