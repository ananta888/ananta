"""Durable Hub repository for content-free local runtime decisions."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Protocol, cast

from agent.common.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.local_model_runtime import (
    LocalRuntimeActivationDecision,
    LocalRuntimeControlReceipt,
)


class LocalRuntimeDecisionConflict(RuntimeError):
    pass


class LocalRuntimeDecisionRepository(Protocol):
    def get_by_request_id(self, request_id: str) -> LocalRuntimeActivationDecision | None: ...
    def get(self, decision_id: str) -> LocalRuntimeActivationDecision | None: ...
    def latest(self) -> LocalRuntimeActivationDecision | None: ...
    def save(self, decision: LocalRuntimeActivationDecision) -> LocalRuntimeActivationDecision: ...
    def control_transaction(self) -> InterProcessFileTransaction: ...
    def get_control_receipt(self, decision_id: str, action: str) -> LocalRuntimeControlReceipt | None: ...
    def save_control_receipt(self, receipt: LocalRuntimeControlReceipt) -> None: ...


class SqliteLocalRuntimeDecisionRepository:
    """Small persistence adapter with cross-process uniqueness constraints."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._control_transaction = InterProcessFileTransaction(self._path.with_name(f"{self._path.name}.control.lock"))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_runtime_decisions (
                    decision_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    decision_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_runtime_control_receipts (
                    decision_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(decision_id, action)
                )
                """
            )
        os.chmod(self._path, 0o600)

    def control_transaction(self) -> InterProcessFileTransaction:
        return self._control_transaction

    def get_control_receipt(self, decision_id: str, action: str) -> LocalRuntimeControlReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM local_runtime_control_receipts WHERE decision_id = ? AND action = ?",
                (decision_id, action),
            ).fetchone()
        if row is None:
            return None
        try:
            return cast(
                LocalRuntimeControlReceipt,
                LocalRuntimeControlReceipt.model_validate_json(str(row["payload_json"])),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LocalRuntimeDecisionConflict("local_runtime_control_receipt_corrupt") from exc

    def save_control_receipt(self, receipt: LocalRuntimeControlReceipt) -> None:
        payload = json.dumps(
            receipt.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM local_runtime_control_receipts WHERE decision_id = ? AND action = ?",
                (receipt.decision_id, receipt.action),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != payload:
                    raise LocalRuntimeDecisionConflict("local_runtime_control_receipt_conflict")
                return
            connection.execute(
                "INSERT INTO local_runtime_control_receipts(decision_id, action, payload_json) VALUES (?, ?, ?)",
                (receipt.decision_id, receipt.action, payload),
            )

    def get_by_request_id(self, request_id: str) -> LocalRuntimeActivationDecision | None:
        return self._select("request_id", request_id)

    def get(self, decision_id: str) -> LocalRuntimeActivationDecision | None:
        return self._select("decision_id", decision_id)

    def latest(self) -> LocalRuntimeActivationDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM local_runtime_decisions ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        try:
            return cast(
                LocalRuntimeActivationDecision,
                LocalRuntimeActivationDecision.model_validate_json(str(row["payload_json"])),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LocalRuntimeDecisionConflict("local_runtime_decision_corrupt") from exc

    def _select(self, column: str, value: str) -> LocalRuntimeActivationDecision | None:
        if column not in {"request_id", "decision_id"}:
            raise ValueError("local_runtime_decision_lookup_invalid")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM local_runtime_decisions WHERE {column} = ?",  # noqa: S608 - closed column allowlist
                (str(value),),
            ).fetchone()
        if row is None:
            return None
        try:
            return cast(
                LocalRuntimeActivationDecision,
                LocalRuntimeActivationDecision.model_validate_json(str(row["payload_json"])),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LocalRuntimeDecisionConflict("local_runtime_decision_corrupt") from exc

    def save(self, decision: LocalRuntimeActivationDecision) -> LocalRuntimeActivationDecision:
        payload = json.dumps(decision.to_wire(), sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT decision_digest, payload_json FROM local_runtime_decisions WHERE request_id = ?",
                (decision.request_id,),
            ).fetchone()
            if existing is not None:
                return cast(
                    LocalRuntimeActivationDecision,
                    LocalRuntimeActivationDecision.model_validate_json(str(existing["payload_json"])),
                )
            try:
                connection.execute(
                    "INSERT INTO local_runtime_decisions"
                    "(decision_id, request_id, decision_digest, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (decision.decision_id, decision.request_id, decision.decision_digest, payload),
                )
            except sqlite3.IntegrityError as exc:
                raise LocalRuntimeDecisionConflict("local_runtime_decision_conflict") from exc
        return decision


__all__ = [
    "LocalRuntimeDecisionConflict",
    "LocalRuntimeDecisionRepository",
    "SqliteLocalRuntimeDecisionRepository",
]
