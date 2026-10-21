"""ANANTA_AGENT_COUNT: how many agent processes one worker container runs.

The container model allows a configurable agent count per worker container
(`AGENTS.md` "Container Model"). These tests source the entrypoint the way the
image contract test does and pin the count contract, the per-process isolation
(port, name, URL, DATA_DIR) and the unchanged single-process default.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "scripts" / "quickstart-single-image-entrypoint.sh"


def _source(body: str, env: dict[str, str | None]) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    # Host shells (.envrc) export agent settings; every run must start clean.
    for key in (
        "ANANTA_AGENT_COUNT",
        "DATA_DIR",
        "AGENT_NAME",
        "AGENT_URL",
        "PORT",
        "ROLE",
        "ANANTA_KNOWLEDGE_INDEX_WORKER_ID",
        "ANANTA_RUN_DB_MIGRATIONS",
    ):
        environment.pop(key, None)
    for key, value in env.items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = str(value)
    return subprocess.run(
        ["bash", "-c", 'source "$1"; ' + body, "agent-count-test", str(ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _fake_python(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "agents.log"
    fake_python = fake_bin / "python"
    fake_python.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "ROLE=$ROLE|PORT=$PORT|NAME=$AGENT_NAME|URL=$AGENT_URL'
        '|DATA=$DATA_DIR|KNOW=$ANANTA_KNOWLEDGE_INDEX_WORKER_ID" >> "$AGENT_COUNT_TEST_LOG"\n'
        "sleep 0.3\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    return fake_bin


def test_count_defaults_to_one():
    result = _source("validate_agent_count", {})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("1")


def test_count_accepts_three():
    result = _source("validate_agent_count", {"ANANTA_AGENT_COUNT": "3"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("3")


def test_count_rejects_non_numeric_and_out_of_range():
    for value in ("abc", "0", "-2", "17"):
        result = _source("validate_agent_count", {"ANANTA_AGENT_COUNT": value})
        assert result.returncode == 64, (value, result.stdout, result.stderr)
        assert "ANANTA_AGENT_COUNT" in result.stderr


def test_worker_port_and_name_indexing():
    result = _source(
        'worker_port_for_index 5000 3; worker_name_for_index w 1; worker_name_for_index w 2',
        {},
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0].endswith("5002")
    assert lines[1].endswith("w")
    assert lines[2].endswith("w-2")


def test_worker_url_swaps_the_port():
    result = _source(
        'worker_url_for_index http://ai-agent-alpha:5000 5001',
        {},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("http://ai-agent-alpha:5001")


def test_worker_url_requires_a_port_suffix():
    result = _source('worker_url_for_index http://ai-agent-alpha 5001', {})
    assert result.returncode == 64
    assert "AGENT_URL must end in" in result.stderr


def test_single_process_default_layout_is_unchanged(tmp_path):
    fake_bin = _fake_python(tmp_path)
    log = tmp_path / "agents.log"
    result = _source(
        'prepare_runtime_directories() { :; }; run_worker',
        {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AGENT_COUNT_TEST_LOG": str(log),
            "AGENT_NAME": "w",
            "AGENT_URL": "http://ai-agent-alpha:5000",
            "ANANTA_RUN_DB_MIGRATIONS": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    entries = log.read_text(encoding="utf-8").splitlines()
    assert len(entries) == 1
    assert "ROLE=worker" in entries[0]
    assert "PORT=5000" in entries[0]
    assert "NAME=w" in entries[0]
    assert "URL=http://ai-agent-alpha:5000" in entries[0]
    # no DATA_DIR fan-out on the default path
    assert "DATA=|" in entries[0]


def test_multiple_processes_get_isolated_identities(tmp_path):
    fake_bin = _fake_python(tmp_path)
    log = tmp_path / "agents.log"
    result = _source(
        'prepare_runtime_directories() { :; }; run_worker',
        {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AGENT_COUNT_TEST_LOG": str(log),
            "ANANTA_AGENT_COUNT": "3",
            "AGENT_NAME": "w",
            "AGENT_URL": "http://ai-agent-alpha:5000",
            "DATA_DIR": str(tmp_path / "data"),
            "ANANTA_KNOWLEDGE_INDEX_WORKER_ID": "kid",
            "ANANTA_RUN_DB_MIGRATIONS": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    entries = log.read_text(encoding="utf-8").splitlines()
    assert len(entries) == 3, entries
    ports = [entry.split("PORT=")[1].split("|")[0] for entry in entries]
    names = [entry.split("NAME=")[1].split("|")[0] for entry in entries]
    urls = [entry.split("URL=")[1].split("|")[0] for entry in entries]
    data_dirs = [entry.split("DATA=")[1].split("|")[0] for entry in entries]
    know_ids = [entry.split("KNOW=")[1] for entry in entries]
    assert ports == ["5000", "5001", "5002"]
    assert names == ["w", "w-2", "w-3"]
    assert urls == [
        "http://ai-agent-alpha:5000",
        "http://ai-agent-alpha:5001",
        "http://ai-agent-alpha:5002",
    ]
    assert data_dirs == [
        str(tmp_path / "data" / "agent-1"),
        str(tmp_path / "data" / "agent-2"),
        str(tmp_path / "data" / "agent-3"),
    ]
    # the knowledge-index identity follows the per-process name
    assert know_ids == ["w", "w-2", "w-3"]
    assert all("ROLE=worker" in entry for entry in entries)


def test_multiple_processes_require_agent_url(tmp_path):
    result = _source(
        'prepare_runtime_directories() { :; }; run_worker',
        {
            "ANANTA_AGENT_COUNT": "2",
            "ANANTA_RUN_DB_MIGRATIONS": "0",
        },
    )
    assert result.returncode == 64
    assert "AGENT_URL is required" in result.stderr


def test_hub_stays_single_process_and_says_so(tmp_path):
    fake_bin = _fake_python(tmp_path)
    log = tmp_path / "agents.log"
    result = _source(
        'prepare_runtime_directories() { :; }; run_hub',
        {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AGENT_COUNT_TEST_LOG": str(log),
            "ANANTA_AGENT_COUNT": "2",
            "ANANTA_RUN_DB_MIGRATIONS": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    entries = log.read_text(encoding="utf-8").splitlines()
    assert len(entries) == 1
    assert "ROLE=hub" in entries[0]
    assert "the hub runs a single process" in result.stdout
