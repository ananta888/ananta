"""Shared bounded primitives for SFU broadcast gate scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

JSON_BYTES_MAX = 4 * 1024 * 1024
_ADDRESS_SPACE_HEADROOM_BYTES = 2 * 1024 * 1024 * 1024
_ADDRESS_SPACE_FLOOR_BYTES = 64 * 1024 * 1024 * 1024
_ADDRESS_SPACE_MULTIPLIER = 4
_PROCESS_GROUP_RSS_POLL_SECONDS = 0.05
_PAGE_SIZE_BYTES = os.sysconf("SC_PAGE_SIZE")
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PRIVATE_IP = re.compile(
    r"(?<!\d)(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)"
)
_SOURCE_RUN_ID = re.compile(r"\b(?:SRC|RUN)_[A-Za-z0-9_-]+\b")
_FORBIDDEN_KEYS = frozenset(
    {
        "raw_payload",
        "plaintext",
        "secret_value",
        "credential_value",
        "media_bytes",
        "transcript_text",
        "key_material",
        "sdp",
        "ice_candidate",
    }
)


class SfuBroadcastGateError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class BoundedCommandResult:
    exit_code: int | None
    timed_out: bool
    elapsed_ms: int
    cpu_seconds: float
    peak_rss_bytes: int


def read_bounded_json(path: Path, *, maximum_bytes: int = JSON_BYTES_MAX) -> Mapping[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum_bytes:
            raise SfuBroadcastGateError("gate_input_unavailable")
        value = json.loads(path.read_text(encoding="utf-8"))
    except SfuBroadcastGateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SfuBroadcastGateError("gate_input_invalid") from exc
    if not isinstance(value, Mapping):
        raise SfuBroadcastGateError("gate_input_invalid")
    return value


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def digest_paths(root: Path, relative_paths: Iterable[str]) -> str:
    digest = hashlib.sha256()
    paths = sorted(set(relative_paths))
    if not paths:
        raise SfuBroadcastGateError("gate_source_set_empty")
    for relative in paths:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SfuBroadcastGateError("gate_source_path_unsafe")
        resolved = root / candidate
        if not resolved.is_file() or resolved.is_symlink():
            raise SfuBroadcastGateError("gate_source_file_missing")
        digest.update(candidate.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(resolved.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def scan_content_free_document(
    value: Any,
    *,
    known_secrets: Sequence[str] = (),
) -> tuple[str, ...]:
    reasons: set[str] = set()

    def visit(item: Any, path: tuple[str, ...]) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                normalized = str(key).casefold()
                if normalized in _FORBIDDEN_KEYS:
                    reasons.add("gate_content_field_forbidden")
                visit(nested, (*path, str(key)))
            return
        if isinstance(item, (list, tuple)):
            for index, nested in enumerate(item):
                visit(nested, (*path, str(index)))
            return
        if isinstance(item, str):
            if len(item.encode("utf-8")) > 1024:
                reasons.add("gate_string_value_unbounded")
            if any(secret and secret in item for secret in known_secrets):
                reasons.add("gate_known_secret_detected")
            if "-----BEGIN " in item or "Bearer " in item or _JWT.search(item):
                reasons.add("gate_credential_pattern_detected")
            if _EMAIL.search(item):
                reasons.add("gate_pii_pattern_detected")
            if _PRIVATE_IP.search(item):
                reasons.add("gate_private_network_identifier_detected")
            if _SOURCE_RUN_ID.search(item):
                reasons.add("gate_unverified_source_run_identifier_detected")

    visit(value, ())
    return tuple(sorted(reasons))


def atomic_write_report(path: Path, report: Mapping[str, Any]) -> None:
    reasons = scan_content_free_document(report)
    if reasons:
        raise SfuBroadcastGateError(reasons[0])
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _address_space_limit_bytes(memory_bytes_max: int) -> int:
    """Return a finite VAS guard without treating sparse mappings as resident memory."""
    return max(
        _ADDRESS_SPACE_FLOOR_BYTES,
        memory_bytes_max * _ADDRESS_SPACE_MULTIPLIER,
        memory_bytes_max + _ADDRESS_SPACE_HEADROOM_BYTES,
    )


def _process_group_rss_bytes(process_group_id: int) -> int | None:
    """Return aggregate resident pages for a Linux process group."""
    try:
        entries = os.scandir("/proc")
    except OSError:
        return None

    total_pages = 0
    with entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                stat = Path(entry.path, "stat").read_text(encoding="ascii")
                fields = stat[stat.rfind(")") + 2:].split()
                if len(fields) > 21 and int(fields[2]) == process_group_id:
                    total_pages += max(0, int(fields[21]))
            except (OSError, UnicodeError, ValueError):
                continue
    return total_pages * _PAGE_SIZE_BYTES


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        process.kill()


def run_bounded_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
    cpu_seconds_max: int,
    memory_bytes_max: int,
) -> BoundedCommandResult:
    if not command or timeout_seconds < 1 or cpu_seconds_max < 1 or memory_bytes_max < 64 * 1024 * 1024:
        raise SfuBroadcastGateError("gate_command_limits_invalid")

    def apply_limits() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds_max, cpu_seconds_max + 1))
        address_space_limit = _address_space_limit_bytes(memory_bytes_max)
        current_hard_limit = resource.getrlimit(resource.RLIMIT_AS)[1]
        if current_hard_limit != resource.RLIM_INFINITY:
            address_space_limit = min(address_space_limit, current_hard_limit)
        resource.setrlimit(resource.RLIMIT_AS, (address_space_limit, address_space_limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    timed_out = False
    accounting_unavailable = False
    peak_rss_bytes = 0
    exit_code: int | None
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=apply_limits,
    )
    deadline = started + timeout_seconds
    while True:
        current_rss_bytes = _process_group_rss_bytes(process.pid)
        if current_rss_bytes is None:
            accounting_unavailable = True
            _kill_process_group(process)
            process.wait()
            exit_code = None
            break
        peak_rss_bytes = max(peak_rss_bytes, current_rss_bytes)
        if current_rss_bytes > memory_bytes_max:
            _kill_process_group(process)
            process.wait()
            exit_code = -signal.SIGKILL
            break

        return_code = process.poll()
        if return_code is not None:
            _kill_process_group(process)
            exit_code = return_code
            break

        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            timed_out = True
            _kill_process_group(process)
            process.wait()
            exit_code = None
            break
        time.sleep(min(_PROCESS_GROUP_RSS_POLL_SECONDS, remaining_seconds))

    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if accounting_unavailable:
        raise SfuBroadcastGateError("gate_memory_accounting_unavailable")
    elapsed_ms = int((time.monotonic() - started) * 1000)
    cpu_seconds = max(
        0.0,
        (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime),
    )
    return BoundedCommandResult(
        exit_code=exit_code,
        timed_out=timed_out,
        elapsed_ms=elapsed_ms,
        cpu_seconds=round(cpu_seconds, 6),
        peak_rss_bytes=peak_rss_bytes,
    )


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "BoundedCommandResult",
    "SfuBroadcastGateError",
    "atomic_write_report",
    "canonical_sha256",
    "digest_paths",
    "read_bounded_json",
    "run_bounded_command",
    "scan_content_free_document",
    "utc_now",
]
