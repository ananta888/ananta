#!/usr/bin/env python3
"""Exercise the shared relay store across real Hub processes and PostgreSQL.

The public report is deliberately content-free and deterministic.  Worker
processes exchange only synthetic identifiers with this supervisor; no relay
payload or database credential is copied into the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

POSTGRES_IMAGE = (
    "postgres:16.13@sha256:"
    "5a65324fe84dc41709ff914e90b07f3e2f577073ed27bf917d4873aca0c9ec51"
)
POSTGRES_IMAGE_ID = "sha256:5a65324fe84dc41709ff914e90b07f3e2f577073ed27bf917d4873aca0c9ec51"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/semantic-relay-multi-hub.json"
SOURCE_FILES = (
    "agent/db_models/semantic_relay.py",
    "agent/repositories/semantic_relay_repository.py",
    "agent/repositories/semantic_relay_shared_store.py",
    "agent/services/semantic_relay_limits.py",
    "migrations/versions/d8e9f0a1b2c3_add_semantic_relay_store.py",
    "migrations/versions/e9f0a1b2c3d4_add_semantic_relay_wire_metadata.py",
    "scripts/e2e/semantic_relay_multi_hub_e2e.py",
)
FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "audio",
        "ciphertext",
        "database_url",
        "password",
        "payload",
        "plaintext",
        "transcript",
    }
)


def _source_hash() -> str:
    digest = hashlib.sha256()
    for relative in SOURCE_FILES:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((ROOT / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _assert_content_free(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_REPORT_KEYS:
                joined = ".".join((*path, str(key)))
                raise RuntimeError(f"multi_hub_report_forbidden_field:{joined}")
            _assert_content_free(nested, (*path, str(key)))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_content_free(nested, (*path, str(index)))


def _limits_from_args(args: argparse.Namespace):
    from agent.services.semantic_relay_limits import SemanticRelayLimits

    return SemanticRelayLimits(
        max_request_bytes=1_500_000,
        max_batch_count=1_000,
        max_session_messages=args.max_session_messages,
        max_session_bytes=args.max_session_bytes,
        max_peer_messages=args.max_peer_messages,
        max_peer_bytes=args.max_peer_bytes,
        max_global_messages=args.max_global_messages,
        max_global_bytes=args.max_global_bytes,
        priority_reserve_messages=1,
        priority_reserve_bytes=1,
        max_sessions=args.max_sessions,
        max_peers_per_session=args.max_peers_per_session,
        max_poll_per_minute=1_000,
        retention_seconds=300,
    )


def _repository(args: argparse.Namespace):
    # Imported only after a child receives DATABASE_URL. agent.database binds
    # the engine at module import time by design.
    from agent.repositories.semantic_relay_shared_store import SharedSemanticRelayRepository

    return SharedSemanticRelayRepository(_limits_from_args(args))


def _append_worker(args: argparse.Namespace) -> int:
    from agent.repositories.semantic_relay_repository import (
        SemanticRelayEnvelope,
        SemanticRelayRepositoryError,
    )

    repository = _repository(args)
    successes = 0
    failures: dict[str, int] = {}
    with Path(args.progress_file).open("a", encoding="utf-8", buffering=1) as progress:
        for index in range(args.start, args.end):
            message_id = f"{args.prefix}-{index}"
            try:
                row = repository.append(
                    SemanticRelayEnvelope(
                        message_id=message_id,
                        tenant_id=args.tenant,
                        session_id=args.session,
                        epoch=1,
                        sender_id="synthetic-hub-sender",
                        audience_id=args.audience,
                        traffic_class="control",
                        payload_bytes=args.payload_bytes,
                        payload_digest=hashlib.sha256(message_id.encode()).hexdigest(),
                        ciphertext="opaque-test-envelope",
                        expires_at=args.expires_at,
                    ),
                    now=args.now,
                )
            except SemanticRelayRepositoryError as exc:
                failures[exc.reason_code] = failures.get(exc.reason_code, 0) + 1
                progress.write(json.dumps({"event": "rejected", "code": exc.reason_code}) + "\n")
            else:
                successes += 1
                progress.write(json.dumps({"event": "committed", "cursor": row.cursor}) + "\n")
            progress.flush()
            os.fsync(progress.fileno())
            if args.delay_ms:
                time.sleep(args.delay_ms / 1_000)
    print(json.dumps({"successes": successes, "failures": failures}, sort_keys=True), flush=True)
    return 0


def _snapshot_worker(args: argparse.Namespace) -> int:
    rows = _repository(args).read_after(
        tenant_id=args.tenant,
        session_id=args.session,
        audience_id=args.audience,
        traffic_class="control",
        cursor=0,
        limit=1_000,
        now=args.now,
    )
    print(
        json.dumps(
            {
                "count": len(rows),
                "cursors": [row.cursor for row in rows],
                "message_ids": sorted(row.message_id for row in rows),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def _ack_worker(args: argparse.Namespace) -> int:
    acknowledged = _repository(args).acknowledge(
        tenant_id=args.tenant,
        session_id=args.session,
        audience_id=args.audience,
        traffic_class="control",
        cursor=args.cursor,
        now=args.now,
    )
    print(json.dumps({"acknowledged": acknowledged}, sort_keys=True), flush=True)
    return 0


def _revoke_worker(args: argparse.Namespace) -> int:
    removed = _repository(args).revoke(
        tenant_id=args.tenant,
        session_id=args.session,
        message_id=args.message_id,
    )
    print(json.dumps({"removed": removed}, sort_keys=True), flush=True)
    return 0


def _expire_worker(args: argparse.Namespace) -> int:
    removed = _repository(args).expire(now=args.now, limit=1_000)
    print(json.dumps({"removed": removed}, sort_keys=True), flush=True)
    return 0


def _run(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=ROOT,
        env=dict(env) if env is not None else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def _docker(command: Sequence[str], *, env: Mapping[str, str] | None = None, timeout: float = 60):
    return _run(("docker", *command), env=env, timeout=timeout)


def _wait_postgres(container: str) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        probe = _run(
            ("docker", "exec", container, "pg_isready", "-U", "ananta_gate", "-d", "relay_gate"),
            timeout=5,
            check=False,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.25)
    logs = _run(("docker", "logs", container), timeout=10, check=False)
    raise RuntimeError(f"multi_hub_postgres_not_ready:{logs.stderr[-500:]}")


def _child_base_args(database_env: Mapping[str, str], progress_file: Path) -> list[str]:
    del database_env  # Keeps call sites explicit about the isolated environment.
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--progress-file",
        str(progress_file),
    ]


def _parse_child_result(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"multi_hub_child_no_result:{completed.stderr[-500:]}")
    return json.loads(lines[-1])


def _invoke_child(
    mode: str,
    *,
    database_env: Mapping[str, str],
    progress_file: Path,
    arguments: Sequence[str] = (),
    timeout: float = 60,
) -> dict[str, Any]:
    command = [*_child_base_args(database_env, progress_file), "--worker-mode", mode, *arguments]
    return _parse_child_result(_run(command, env=database_env, timeout=timeout))


def _progress_count(path: Path, event: str = "committed") -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            count += json.loads(line).get("event") == event
        except json.JSONDecodeError:
            continue
    return count


def _append_args(
    *,
    prefix: str,
    start: int,
    end: int,
    tenant: str = "tenant-restart",
    session: str = "session-restart",
    audience: str = "audience-restart",
    now: float = 1_000,
    expires_at: float = 10_000,
    payload_bytes: int = 1,
    delay_ms: int = 0,
    limits: Mapping[str, int] | None = None,
) -> list[str]:
    values = {
        "max-global-messages": 1_000,
        "max-global-bytes": 1_000_000,
        "max-session-messages": 1_000,
        "max-session-bytes": 1_000_000,
        "max-peer-messages": 1_000,
        "max-peer-bytes": 1_000_000,
        "max-sessions": 1_000,
        "max-peers-per-session": 1_000,
    }
    values.update(limits or {})
    result = [
        "--prefix",
        prefix,
        "--start",
        str(start),
        "--end",
        str(end),
        "--tenant",
        tenant,
        "--session",
        session,
        "--audience",
        audience,
        "--now",
        str(now),
        "--expires-at",
        str(expires_at),
        "--payload-bytes",
        str(payload_bytes),
        "--delay-ms",
        str(delay_ms),
    ]
    for name, value in values.items():
        result.extend((f"--{name}", str(value)))
    return result


def _concurrent_append(
    *,
    database_env: Mapping[str, str],
    temporary: Path,
    arguments_a: Sequence[str],
    arguments_b: Sequence[str],
    label: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    paths = (temporary / f"{label}-a.jsonl", temporary / f"{label}-b.jsonl")
    processes: list[subprocess.Popen[str]] = []
    for path, arguments in zip(paths, (arguments_a, arguments_b), strict=True):
        command = [*_child_base_args(database_env, path), "--worker-mode", "append", *arguments]
        processes.append(
            subprocess.Popen(
                command,
                cwd=ROOT,
                env=dict(database_env),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    distinct = processes[0].pid != processes[1].pid
    results: list[dict[str, Any]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        if process.returncode != 0:
            raise RuntimeError(f"multi_hub_append_failed:{stderr[-500:]}")
        completed = subprocess.CompletedProcess([], process.returncode, stdout, stderr)
        results.append(_parse_child_result(completed))
    return results[0], results[1], distinct


def _quota_race(
    *,
    database_env: Mapping[str, str],
    temporary: Path,
    label: str,
    arguments_a: Sequence[str],
    arguments_b: Sequence[str],
    expected_successes: int,
    rejection_code: str,
    cleanup_tenant: str,
    cleanup_session: str,
) -> bool:
    first, second, _ = _concurrent_append(
        database_env=database_env,
        temporary=temporary,
        arguments_a=arguments_a,
        arguments_b=arguments_b,
        label=label,
    )
    successes = int(first["successes"]) + int(second["successes"])
    failures: dict[str, int] = {}
    for result in (first, second):
        for code, count in result["failures"].items():
            failures[code] = failures.get(code, 0) + int(count)
    _invoke_child(
        "revoke",
        database_env=database_env,
        progress_file=temporary / f"{label}-cleanup.jsonl",
        arguments=("--tenant", cleanup_tenant, "--session", cleanup_session),
    )
    attempts = 0
    for raw_arguments in (arguments_a, arguments_b):
        parsed_arguments = list(raw_arguments)
        start = int(parsed_arguments[parsed_arguments.index("--start") + 1])
        end = int(parsed_arguments[parsed_arguments.index("--end") + 1])
        attempts += len(range(start, end))
    return successes == expected_successes and failures == {rejection_code: attempts - expected_successes}


def _success_document(checks: Mapping[str, bool]) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "ananta.semantic-relay-multi-hub-gate.v1",
        "gate": "ASMP-TRN-007",
        "source_sha256": _source_hash(),
        "source_files": list(SOURCE_FILES),
        "runtime": {
            "database": "PostgreSQL 16.13",
            "image": POSTGRES_IMAGE,
            "image_id": POSTGRES_IMAGE_ID,
            "hub_processes": 2,
            "abort_signal": "SIGKILL",
        },
        "measurements": {
            "restart_flow_messages": 40,
            "minimum_commits_before_abort": 5,
            "quota_race_processes": 2,
            "remaining_after_cleanup": 0,
        },
        "checks": dict(checks),
        "content_policy": {
            "content_free": True,
            "forbidden_fields": sorted(FORBIDDEN_REPORT_KEYS),
        },
        "passed": bool(checks) and all(checks.values()),
    }
    _assert_content_free(document)
    return document


def _execute_live(output: Path) -> dict[str, Any]:
    # A fresh reference host may not have the immutable image yet.  Pull it as
    # an explicit bounded preflight so the subsequent attestation observes the
    # image that will actually run instead of permanently recording a miss.
    _docker(("pull", POSTGRES_IMAGE), timeout=600)
    inspect = _docker(("image", "inspect", POSTGRES_IMAGE, "--format", "{{.Id}}"))
    exact_image = inspect.stdout.strip() == POSTGRES_IMAGE_ID
    container = f"ananta-relay-gate-{uuid.uuid4().hex[:12]}"
    password = secrets.token_urlsafe(24)
    docker_env = {**os.environ, "POSTGRES_PASSWORD": password}
    database_env: dict[str, str]
    started = False
    try:
        _docker(
            (
                "run",
                "--rm",
                "--detach",
                "--name",
                container,
                "--read-only",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
                "--tmpfs",
                "/var/run/postgresql:rw,noexec,nosuid,size=16m",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "--env",
                "POSTGRES_PASSWORD",
                "--env",
                "POSTGRES_USER=ananta_gate",
                "--env",
                "POSTGRES_DB=relay_gate",
                "--publish",
                "127.0.0.1::5432",
                POSTGRES_IMAGE,
            ),
            env=docker_env,
            timeout=90,
        )
        started = True
        _wait_postgres(container)
        port_line = _docker(("port", container, "5432/tcp")).stdout.strip().splitlines()[0]
        port = int(port_line.rsplit(":", 1)[1])
        database_env = {
            **os.environ,
            "DATABASE_URL": f"postgresql://ananta_gate:{password}@127.0.0.1:{port}/relay_gate",
            "ROLE": "hub",
            "DISABLE_INITIAL_ADMIN": "true",
        }
        migration = _run(
            (sys.executable, "-m", "alembic", "upgrade", "head"),
            env=database_env,
            timeout=180,
            check=False,
        )
        migration_ok = migration.returncode == 0
        if not migration_ok:
            raise RuntimeError(f"multi_hub_migration_failed:{migration.stderr[-800:]}")

        with tempfile.TemporaryDirectory(prefix="ananta-relay-gate-") as directory:
            temporary = Path(directory)
            slow_path = temporary / "restart-a.jsonl"
            fast_path = temporary / "restart-b.jsonl"
            slow_command = [
                *_child_base_args(database_env, slow_path),
                "--worker-mode",
                "append",
                *_append_args(prefix="restart", start=0, end=20, delay_ms=75),
            ]
            fast_command = [
                *_child_base_args(database_env, fast_path),
                "--worker-mode",
                "append",
                *_append_args(prefix="restart", start=20, end=40, delay_ms=5),
            ]
            slow = subprocess.Popen(
                slow_command,
                cwd=ROOT,
                env=database_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            fast = subprocess.Popen(
                fast_command,
                cwd=ROOT,
                env=database_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            distinct_hubs = slow.pid != fast.pid
            deadline = time.monotonic() + 30
            while _progress_count(slow_path) < 5 and time.monotonic() < deadline and slow.poll() is None:
                time.sleep(0.02)
            killed_after_commits = _progress_count(slow_path) >= 5 and slow.poll() is None
            if killed_after_commits:
                os.kill(slow.pid, signal.SIGKILL)
            slow.communicate(timeout=10)
            fast_stdout, fast_stderr = fast.communicate(timeout=60)
            if fast.returncode != 0:
                raise RuntimeError(f"multi_hub_survivor_failed:{fast_stderr[-500:]}")
            _parse_child_result(subprocess.CompletedProcess([], fast.returncode, fast_stdout, fast_stderr))

            restart = _invoke_child(
                "append",
                database_env=database_env,
                progress_file=temporary / "restart-recovery.jsonl",
                arguments=_append_args(prefix="restart", start=0, end=20),
            )
            snapshot = _invoke_child(
                "snapshot",
                database_env=database_env,
                progress_file=temporary / "snapshot.jsonl",
                arguments=(
                    "--tenant",
                    "tenant-restart",
                    "--session",
                    "session-restart",
                    "--audience",
                    "audience-restart",
                    "--now",
                    "1001",
                ),
            )
            expected_ids = sorted(f"restart-{index}" for index in range(40))
            monotone_restart = snapshot["cursors"] == list(range(1, 41))
            no_lost_entries = snapshot["message_ids"] == expected_ids
            duplicate_idempotent = int(restart["successes"]) == 20 and snapshot["count"] == 40

            first_ack = _invoke_child(
                "ack",
                database_env=database_env,
                progress_file=temporary / "ack-1.jsonl",
                arguments=(
                    "--tenant",
                    "tenant-restart",
                    "--session",
                    "session-restart",
                    "--audience",
                    "audience-restart",
                    "--cursor",
                    "10",
                    "--now",
                    "1002",
                ),
            )
            second_ack = _invoke_child(
                "ack",
                database_env=database_env,
                progress_file=temporary / "ack-2.jsonl",
                arguments=(
                    "--tenant",
                    "tenant-restart",
                    "--session",
                    "session-restart",
                    "--audience",
                    "audience-restart",
                    "--cursor",
                    "10",
                    "--now",
                    "1003",
                ),
            )
            after_ack_restart = _invoke_child(
                "snapshot",
                database_env=database_env,
                progress_file=temporary / "ack-snapshot.jsonl",
                arguments=(
                    "--tenant",
                    "tenant-restart",
                    "--session",
                    "session-restart",
                    "--audience",
                    "audience-restart",
                    "--now",
                    "1004",
                ),
            )
            ack_restart_stable = (
                first_ack["acknowledged"] == second_ack["acknowledged"] == 10
                and after_ack_restart["cursors"] == list(range(11, 41))
            )
            _invoke_child(
                "ack",
                database_env=database_env,
                progress_file=temporary / "ack-all.jsonl",
                arguments=(
                    "--tenant",
                    "tenant-restart",
                    "--session",
                    "session-restart",
                    "--audience",
                    "audience-restart",
                    "--cursor",
                    "40",
                    "--now",
                    "1005",
                ),
            )

            global_count = _quota_race(
                database_env=database_env,
                temporary=temporary,
                label="global-count",
                arguments_a=_append_args(
                    prefix="global-count-a",
                    start=0,
                    end=20,
                    tenant="tenant-global-count",
                    session="session-global-count",
                    limits={"max-global-messages": 19},
                ),
                arguments_b=_append_args(
                    prefix="global-count-b",
                    start=0,
                    end=20,
                    tenant="tenant-global-count",
                    session="session-global-count",
                    limits={"max-global-messages": 19},
                ),
                expected_successes=19,
                rejection_code="relay_global_message_quota",
                cleanup_tenant="tenant-global-count",
                cleanup_session="session-global-count",
            )
            global_bytes = _quota_race(
                database_env=database_env,
                temporary=temporary,
                label="global-bytes",
                arguments_a=_append_args(
                    prefix="global-bytes-a",
                    start=0,
                    end=20,
                    tenant="tenant-global-bytes",
                    session="session-global-bytes",
                    payload_bytes=2,
                    limits={"max-global-bytes": 25},
                ),
                arguments_b=_append_args(
                    prefix="global-bytes-b",
                    start=0,
                    end=20,
                    tenant="tenant-global-bytes",
                    session="session-global-bytes",
                    payload_bytes=2,
                    limits={"max-global-bytes": 25},
                ),
                expected_successes=12,
                rejection_code="relay_global_byte_quota",
                cleanup_tenant="tenant-global-bytes",
                cleanup_session="session-global-bytes",
            )
            peer_limit = _quota_race(
                database_env=database_env,
                temporary=temporary,
                label="peer-count",
                arguments_a=_append_args(
                    prefix="peer-a",
                    start=0,
                    end=20,
                    tenant="tenant-peer",
                    session="session-peer",
                    audience="audience-peer",
                    limits={"max-peer-messages": 13},
                ),
                arguments_b=_append_args(
                    prefix="peer-b",
                    start=0,
                    end=20,
                    tenant="tenant-peer",
                    session="session-peer",
                    audience="audience-peer",
                    limits={"max-peer-messages": 13},
                ),
                expected_successes=13,
                rejection_code="relay_peer_message_quota",
                cleanup_tenant="tenant-peer",
                cleanup_session="session-peer",
            )
            session_limit = _quota_race(
                database_env=database_env,
                temporary=temporary,
                label="session-count",
                arguments_a=_append_args(
                    prefix="session-a",
                    start=0,
                    end=20,
                    tenant="tenant-session",
                    session="session-limited",
                    audience="audience-a",
                    limits={"max-session-messages": 17},
                ),
                arguments_b=_append_args(
                    prefix="session-b",
                    start=0,
                    end=20,
                    tenant="tenant-session",
                    session="session-limited",
                    audience="audience-b",
                    limits={"max-session-messages": 17},
                ),
                expected_successes=17,
                rejection_code="relay_session_message_quota",
                cleanup_tenant="tenant-session",
                cleanup_session="session-limited",
            )

            lifecycle_args = _append_args(
                prefix="lifecycle",
                start=0,
                end=2,
                tenant="tenant-lifecycle",
                session="session-lifecycle",
                audience="audience-lifecycle",
            )
            _invoke_child(
                "append",
                database_env=database_env,
                progress_file=temporary / "lifecycle.jsonl",
                arguments=lifecycle_args,
            )
            one_revoke = _invoke_child(
                "revoke",
                database_env=database_env,
                progress_file=temporary / "revoke-one.jsonl",
                arguments=(
                    "--tenant",
                    "tenant-lifecycle",
                    "--session",
                    "session-lifecycle",
                    "--message-id",
                    "lifecycle-0",
                ),
            )["removed"]
            repeat_revoke = _invoke_child(
                "revoke",
                database_env=database_env,
                progress_file=temporary / "revoke-repeat.jsonl",
                arguments=(
                    "--tenant",
                    "tenant-lifecycle",
                    "--session",
                    "session-lifecycle",
                    "--message-id",
                    "lifecycle-0",
                ),
            )["removed"]
            session_end = _invoke_child(
                "revoke",
                database_env=database_env,
                progress_file=temporary / "session-end.jsonl",
                arguments=("--tenant", "tenant-lifecycle", "--session", "session-lifecycle"),
            )["removed"]
            repeat_session_end = _invoke_child(
                "revoke",
                database_env=database_env,
                progress_file=temporary / "session-end-repeat.jsonl",
                arguments=("--tenant", "tenant-lifecycle", "--session", "session-lifecycle"),
            )["removed"]
            revoke_restart_stable = (one_revoke, repeat_revoke, session_end, repeat_session_end) == (1, 0, 1, 0)

            _invoke_child(
                "append",
                database_env=database_env,
                progress_file=temporary / "expired.jsonl",
                arguments=_append_args(
                    prefix="expired",
                    start=0,
                    end=1,
                    tenant="tenant-expire",
                    session="session-expire",
                    audience="audience-expire",
                    now=2_000,
                    expires_at=2_001,
                ),
            )
            first_expire = _invoke_child(
                "expire",
                database_env=database_env,
                progress_file=temporary / "expire-1.jsonl",
                arguments=("--now", "2002"),
            )["removed"]
            second_expire = _invoke_child(
                "expire",
                database_env=database_env,
                progress_file=temporary / "expire-2.jsonl",
                arguments=("--now", "2002"),
            )["removed"]
            expire_restart_stable = (first_expire, second_expire) == (1, 0)

            final_snapshot = _invoke_child(
                "snapshot",
                database_env=database_env,
                progress_file=temporary / "final-snapshot.jsonl",
                arguments=(
                    "--tenant",
                    "tenant-restart",
                    "--session",
                    "session-restart",
                    "--audience",
                    "audience-restart",
                    "--now",
                    "2002",
                ),
            )
            checks = {
                "exact_postgres_digest": exact_image,
                "migration_head_applied": migration_ok,
                "two_independent_hub_processes": distinct_hubs,
                "survivor_continued_after_peer_abort": killed_after_commits,
                "restart_cursor_monotone": monotone_restart,
                "restart_no_lost_entries": no_lost_entries,
                "duplicate_append_idempotent": duplicate_idempotent,
                "ack_idempotent_restart_stable": ack_restart_stable,
                "global_count_atomic": global_count,
                "global_bytes_atomic": global_bytes,
                "peer_limit_atomic": peer_limit,
                "session_limit_atomic": session_limit,
                "revoke_and_session_end_restart_stable": revoke_restart_stable,
                "ttl_expire_restart_stable": expire_restart_stable,
                "bounded_cleanup_complete": final_snapshot["count"] == 0,
            }
            document = _success_document(checks)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return document
    finally:
        if started:
            _run(("docker", "rm", "--force", container), timeout=30, check=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--worker-mode",
        choices=("append", "snapshot", "ack", "revoke", "expire"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--progress-file", default=os.devnull, help=argparse.SUPPRESS)
    parser.add_argument("--prefix", default="gate", help=argparse.SUPPRESS)
    parser.add_argument("--start", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--end", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--tenant", default="tenant-restart", help=argparse.SUPPRESS)
    parser.add_argument("--session", default="session-restart", help=argparse.SUPPRESS)
    parser.add_argument("--audience", default="audience-restart", help=argparse.SUPPRESS)
    parser.add_argument("--message-id", help=argparse.SUPPRESS)
    parser.add_argument("--cursor", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--now", type=float, default=1_000, help=argparse.SUPPRESS)
    parser.add_argument("--expires-at", type=float, default=10_000, help=argparse.SUPPRESS)
    parser.add_argument("--payload-bytes", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--delay-ms", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--max-global-messages", type=int, default=1_000, help=argparse.SUPPRESS)
    parser.add_argument("--max-global-bytes", type=int, default=1_000_000, help=argparse.SUPPRESS)
    parser.add_argument("--max-session-messages", type=int, default=1_000, help=argparse.SUPPRESS)
    parser.add_argument("--max-session-bytes", type=int, default=1_000_000, help=argparse.SUPPRESS)
    parser.add_argument("--max-peer-messages", type=int, default=1_000, help=argparse.SUPPRESS)
    parser.add_argument("--max-peer-bytes", type=int, default=1_000_000, help=argparse.SUPPRESS)
    parser.add_argument("--max-sessions", type=int, default=1_000, help=argparse.SUPPRESS)
    parser.add_argument("--max-peers-per-session", type=int, default=1_000, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    workers = {
        "append": _append_worker,
        "snapshot": _snapshot_worker,
        "ack": _ack_worker,
        "revoke": _revoke_worker,
        "expire": _expire_worker,
    }
    if args.worker_mode:
        return workers[args.worker_mode](args)
    if args.execute_live:
        document = _execute_live(args.output.resolve())
    elif args.verify:
        if not args.output.exists():
            raise SystemExit("semantic_relay_multi_hub_report_missing")
        document = json.loads(args.output.read_text(encoding="utf-8"))
        _assert_content_free(document)
        if document.get("source_sha256") != _source_hash():
            raise SystemExit("semantic_relay_multi_hub_report_stale")
    else:
        raise SystemExit("select --execute-live or --verify")
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if document.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
