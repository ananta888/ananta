#!/usr/bin/env python3
"""Authenticated fixed-operation bridge for the Hub-owned local runtime."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, cast

from ananta_contracts.local_model_runtime import (
    LocalRuntimeActivationDecision,
    LocalRuntimeControlReceipt,
)

MAX_REQUEST_BYTES = 32 * 1024
ALLOWED_ACTIONS = {
    "activate": ("systemctl", "--user", "start", "ananta-local-model-runtime.service"),
    "deactivate": ("systemctl", "--user", "stop", "ananta-local-model-runtime.service"),
    "restart": ("systemctl", "--user", "restart", "ananta-local-model-runtime.service"),
}


class LocalRuntimeControlHandler(BaseHTTPRequestHandler):
    server_version = "AnantaLocalRuntimeControl/1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/resources":
            self._respond(404, {"reason_code": "route_not_found"})
            return
        if not self._authorized():
            self._respond(401, {"reason_code": "control_unauthorized"})
            return
        try:
            payload = resource_payload(self.server.command_runner)  # type: ignore[attr-defined]
        except RuntimeError as exc:
            self._respond(503, {"reason_code": str(exc)})
            return
        self._respond(200, payload)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/control":
            self._respond(404, {"reason_code": "route_not_found"})
            return
        if not self._authorized():
            self._respond(401, {"reason_code": "control_unauthorized"})
            return
        try:
            body = self._read_body()
            action, decision = validate_control_request(body)
            receipt = apply_control_action(
                action,
                self.server.command_runner,  # type: ignore[attr-defined]
                decision=decision,
                repository=self.server.action_repository,  # type: ignore[attr-defined]
                lock=self.server.action_lock,  # type: ignore[attr-defined]
            )
        except ValueError as exc:
            self._respond(400, {"reason_code": str(exc)})
            return
        except RuntimeError as exc:
            self._respond(503, {"reason_code": str(exc)})
            return
        self._respond(200, receipt.model_dump(mode="json", by_alias=True))

    def _authorized(self) -> bool:
        expected = str(self.server.control_token)  # type: ignore[attr-defined]
        supplied = str(self.headers.get("Authorization") or "")
        return hmac.compare_digest(supplied, f"Bearer {expected}")

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise ValueError("control_content_length_invalid") from exc
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError("control_request_size_invalid")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("control_request_json_invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("control_request_invalid")
        return payload

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalRuntimeControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        control_token: str,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if len(control_token) < 24:
            raise ValueError("local runtime control token must contain at least 24 characters")
        self.control_token = control_token
        self.command_runner = command_runner
        state_dir = Path(
            os.environ.get("ANANTA_LOCAL_MODEL_STATE_DIR") or "/home/krusty/ananta/data/local-model-runtime"
        )
        self.action_repository = LocalRuntimeControlActionRepository(state_dir / "control-actions.sqlite3")
        self.action_lock = threading.RLock()
        super().__init__(address, LocalRuntimeControlHandler)


class LocalRuntimeControlActionRepository:
    """Persistent replay fence; pending actions are never guessed complete."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS control_actions("
                "decision_id TEXT NOT NULL, action TEXT NOT NULL, decision_digest TEXT NOT NULL, "
                "status TEXT NOT NULL, receipt_json TEXT, PRIMARY KEY(decision_id, action))"
            )
        os.chmod(self._path, 0o600)

    def begin(
        self,
        decision: LocalRuntimeActivationDecision,
        action: str,
    ) -> LocalRuntimeControlReceipt | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT decision_digest, status, receipt_json FROM control_actions "
                "WHERE decision_id = ? AND action = ?",
                (decision.decision_id, action),
            ).fetchone()
            if row is not None:
                if not hmac.compare_digest(str(row[0]), decision.decision_digest):
                    raise ValueError("local_runtime_control_replay_digest_mismatch")
                if row[1] == "completed" and row[2]:
                    try:
                        receipt = LocalRuntimeControlReceipt.model_validate_json(str(row[2]))
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError("local_runtime_control_state_corrupt") from exc
                    if (
                        receipt.decision_id != decision.decision_id
                        or receipt.decision_digest != decision.decision_digest
                        or receipt.action != action
                        or receipt.status != "completed"
                    ):
                        raise RuntimeError("local_runtime_control_state_corrupt")
                    return cast(LocalRuntimeControlReceipt, receipt)
                raise RuntimeError("local_runtime_control_state_uncertain")
            connection.execute(
                "INSERT INTO control_actions(decision_id, action, decision_digest, status) VALUES (?, ?, ?, 'pending')",
                (decision.decision_id, action, decision.decision_digest),
            )
        return None

    def complete(self, receipt: LocalRuntimeControlReceipt) -> None:
        payload = receipt.model_dump_json(by_alias=True)
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE control_actions SET status = 'completed', receipt_json = ? "
                "WHERE decision_id = ? AND action = ? AND decision_digest = ? AND status = 'pending'",
                (payload, receipt.decision_id, receipt.action, receipt.decision_digest),
            ).rowcount
        if updated != 1:
            raise RuntimeError("local_runtime_control_state_conflict")

    def clear_failed(self, decision: LocalRuntimeActivationDecision, action: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM control_actions WHERE decision_id = ? AND action = ? "
                "AND decision_digest = ? AND status = 'pending'",
                (decision.decision_id, action, decision.decision_digest),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)


def validate_control_request(payload: dict[str, Any]) -> tuple[str, LocalRuntimeActivationDecision]:
    if set(payload) != {"action", "decision"}:
        raise ValueError("control_request_fields_invalid")
    action = str(payload.get("action") or "").strip().lower()
    if action not in ALLOWED_ACTIONS:
        raise ValueError("local_runtime_control_action_invalid")
    try:
        decision = LocalRuntimeActivationDecision.model_validate(payload.get("decision"))
    except (TypeError, ValueError) as exc:
        raise ValueError("local_runtime_decision_invalid") from exc
    expected_digest = decision_digest(decision)
    if not hmac.compare_digest(expected_digest, decision.decision_digest):
        raise ValueError("local_runtime_decision_digest_mismatch")
    if action in {"activate", "restart"} and not decision.admitted:
        raise ValueError("local_runtime_activation_not_admitted")
    return action, decision


def decision_digest(decision: LocalRuntimeActivationDecision) -> str:
    wire = decision.to_wire()
    unsigned = {
        key: wire[key]
        for key in (
            "request_id",
            "revision",
            "admitted",
            "reason_code",
            "start_order",
            "effective_contexts",
            "total_vram_bytes",
            "free_vram_bytes",
            "available_ram_bytes",
            "required_vram_bytes",
            "reserve_vram_bytes",
            "created_at",
        )
    }
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def apply_control_action(
    action: str,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    *,
    decision: LocalRuntimeActivationDecision,
    repository: LocalRuntimeControlActionRepository,
    lock: threading.RLock,
    state_dir: str | Path | None = None,
) -> LocalRuntimeControlReceipt:
    with lock:
        replay = repository.begin(decision, action)
        if replay is not None:
            return replay
        try:
            run_control_action(
                action,
                command_runner,
                decision=decision,
                state_dir=state_dir,
            )
        except Exception:
            repository.clear_failed(decision, action)
            raise
        receipt = LocalRuntimeControlReceipt(
            decision_id=decision.decision_id,
            decision_digest=decision.decision_digest,
            action=action,
            status="completed",
            reason_code="runtime_control_completed",
            completed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        repository.complete(receipt)
        return receipt


def run_control_action(
    action: str,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    *,
    decision: LocalRuntimeActivationDecision | None = None,
    state_dir: str | Path | None = None,
) -> None:
    command = ALLOWED_ACTIONS.get(action)
    if command is None:
        raise ValueError("local_runtime_control_action_invalid")
    context_path = _context_state_path(state_dir)
    previous = context_path.read_bytes() if context_path.is_file() else None
    if action in {"activate", "restart"}:
        if decision is None or not decision.admitted:
            raise ValueError("local_runtime_activation_decision_required")
        _write_effective_contexts(context_path, decision)
    try:
        command_runner(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if action in {"activate", "restart"}:
            _restore_context_state(context_path, previous)
        raise RuntimeError("local_runtime_control_command_failed") from exc


def _context_state_path(state_dir: str | Path | None) -> Path:
    root = Path(
        state_dir or os.environ.get("ANANTA_LOCAL_MODEL_STATE_DIR") or "/home/krusty/ananta/data/local-model-runtime"
    )
    return root / "active-contexts.env"


def _write_effective_contexts(
    path: Path,
    decision: LocalRuntimeActivationDecision,
) -> None:
    contexts = {item.runtime_id: item.context_tokens for item in decision.effective_contexts}
    if set(contexts) != {"kat", "lfm", "needle"}:
        raise ValueError("local_runtime_effective_context_set_incomplete")
    payload = (
        f"ANANTA_KAT_CTX={contexts['kat']}\n"
        f"ANANTA_LFM_CTX={contexts['lfm']}\n"
        f"ANANTA_NEEDLE_CTX={contexts['needle']}\n"
        f"ANANTA_LOCAL_RUNTIME_DECISION_DIGEST={decision.decision_digest}\n"
    ).encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".active-contexts-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_context_state(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=".active-contexts-restore-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def resource_payload(
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    *,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    try:
        result = command_runner(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        total_mib, free_mib = (int(value.strip()) for value in str(result.stdout or "").splitlines()[0].split(",", 1))
        available_ram_bytes = _available_ram_bytes()
    except (OSError, IndexError, ValueError, subprocess.SubprocessError) as exc:
        raise RuntimeError("local_runtime_resource_snapshot_unavailable") from exc
    return {
        "total_vram_bytes": total_mib * 1024 * 1024,
        "free_vram_bytes": free_mib * 1024 * 1024,
        "available_ram_bytes": available_ram_bytes,
        "runtime_usage": runtime_usage_payload(command_runner, state_dir=state_dir),
        "effective_contexts": active_contexts_payload(state_dir=state_dir),
    }


def active_contexts_payload(*, state_dir: str | Path | None = None) -> dict[str, int]:
    path = _context_state_path(state_dir)
    if not path.is_file():
        return {}
    expected = {
        "ANANTA_KAT_CTX": "kat",
        "ANANTA_LFM_CTX": "lfm",
        "ANANTA_NEEDLE_CTX": "needle",
    }
    contexts: dict[str, int] = {}
    digest_seen = False
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            key, value = line.split("=", 1)
            if key == "ANANTA_LOCAL_RUNTIME_DECISION_DIGEST":
                if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                    raise ValueError
                digest_seen = True
                continue
            runtime_id = expected.get(key)
            if runtime_id is None or not value.isdigit():
                raise ValueError
            contexts[runtime_id] = int(value)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("local_runtime_active_context_state_invalid") from exc
    if set(contexts) != {"kat", "lfm", "needle"} or not digest_seen:
        raise RuntimeError("local_runtime_active_context_state_invalid")
    if (
        not 1 <= contexts["needle"] <= 256
        or not 1 <= contexts["kat"] <= 100_000_000
        or not 1 <= contexts["lfm"] <= 100_000_000
    ):
        raise RuntimeError("local_runtime_active_context_state_invalid")
    return contexts


def runtime_usage_payload(
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    *,
    state_dir: str | Path | None = None,
) -> dict[str, dict[str, int]]:
    root = Path(
        state_dir or os.environ.get("ANANTA_LOCAL_MODEL_STATE_DIR") or "/home/krusty/ananta/data/local-model-runtime"
    )
    pids: dict[str, int] = {}
    result: dict[str, dict[str, int]] = {}
    for runtime_id in ("kat", "lfm", "needle"):
        try:
            raw_pid = (root / f"{runtime_id}.pid").read_text(encoding="ascii").strip()
            if not raw_pid.isdigit() or not 1 <= int(raw_pid) <= 2_147_483_647:
                continue
            pid = int(raw_pid)
            process_ids = _descendant_pids(pid)
            ram_used = sum(_process_rss_bytes(Path("/proc") / str(process_id) / "status") for process_id in process_ids)
        except (OSError, UnicodeError, ValueError):
            continue
        pids[runtime_id] = pid
        result[runtime_id] = {"vram_used_bytes": 0, "ram_used_bytes": ram_used}
    if not pids:
        return result
    try:
        gpu_rows = command_runner(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        by_pid = {
            process_id: runtime_id for runtime_id, root_pid in pids.items() for process_id in _descendant_pids(root_pid)
        }
        for line in str(gpu_rows.stdout or "").splitlines():
            raw_pid, raw_mib = (value.strip() for value in line.split(",", 1))
            matched_runtime_id = by_pid.get(int(raw_pid))
            if matched_runtime_id is not None:
                result[matched_runtime_id]["vram_used_bytes"] += max(0, int(raw_mib)) * 1024 * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        # Per-process GPU accounting can be unavailable while the authoritative
        # total/free measurement remains valid. Zero is explicit, not estimated.
        pass
    return result


def _descendant_pids(root_pid: int, *, proc_root: Path = Path("/proc")) -> frozenset[int]:
    """Return a fixed runtime process and all descendants for resource attribution."""

    parents: dict[int, int] = {}
    try:
        entries = tuple(proc_root.iterdir())
    except OSError as exc:
        raise ValueError("local_runtime_process_tree_unavailable") from exc
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="ascii")
            fields = stat[stat.rfind(")") + 2 :].split()
            parents[int(entry.name)] = int(fields[1])
        except (OSError, UnicodeError, ValueError, IndexError):
            continue
    if root_pid not in parents:
        raise ValueError("local_runtime_process_missing")
    result = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in result and pid not in result:
                result.add(pid)
                changed = True
    return frozenset(result)


def _process_rss_bytes(status_path: Path) -> int:
    with status_path.open(encoding="ascii") as handle:
        for line in handle:
            if line.startswith("VmRSS:"):
                return max(0, int(line.split()[1])) * 1024
    raise ValueError("local_runtime_process_rss_unavailable")


def _available_ram_bytes() -> int:
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, IndexError, ValueError) as exc:
        raise RuntimeError("local_runtime_ram_snapshot_unavailable") from exc
    raise RuntimeError("local_runtime_ram_snapshot_unavailable")


def main() -> None:
    host = str(os.environ.get("ANANTA_LOCAL_MODEL_CONTROL_BIND_HOST") or "127.0.0.1")
    port = int(os.environ.get("ANANTA_LOCAL_MODEL_CONTROL_PORT") or "8093")
    token = str(os.environ.get("ANANTA_LOCAL_MODEL_CONTROL_TOKEN") or "")
    LocalRuntimeControlServer((host, port), control_token=token).serve_forever()


if __name__ == "__main__":
    main()
