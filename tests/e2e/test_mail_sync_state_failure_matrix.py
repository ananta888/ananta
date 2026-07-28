from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import pytest

from agent.services.jmap_http_transport import (
    JmapHttpRequest,
    JmapHttpResponse,
    JmapHttpTransport,
)
from tests.e2e.mail_ananta_composition_harness import (
    AnantaMailCompositionHarness,
    ContractJmapAdapter,
    InProcessIntentHub,
    assert_reference_only_results,
)
from worker import mail_task_composition


ROOT = Path(__file__).resolve().parents[2]
SECRET_ROOT = ROOT / "tests/fixtures/jmap/secrets"
PASSWORD_FILE = SECRET_ROOT / "alice-password"


class StateFailureJmapAdapter(ContractJmapAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.mode = "initial"

    def configure_paged_changes(self) -> None:
        self.mode = "paged"
        self.messages["E3"] = self._message(
            email_id="E3",
            subject="Paged change",
            message_id="paged-change@example.test",
            keywords={},
        )
        self.email_state = "email-state-3"
        self.query_state = "query-state-3"
        self.mailbox_state = "mailbox-state-2"

    def configure_rebuild(self) -> None:
        self.mode = "cannot-calculate"
        self.messages.pop("E2", None)
        self.messages["E4"] = self._message(
            email_id="E4",
            subject="Rebuild replacement",
            message_id="rebuild-replacement@example.test",
            keywords={"$flagged": True},
        )
        self.email_state = "email-state-4"
        self.query_state = "query-state-4"
        self.mailbox_state = "mailbox-state-3"

    def configure_single_change(self) -> None:
        self.mode = "single"
        self.messages["E3"] = self._message(
            email_id="E3",
            subject="Resume exactly once",
            message_id="resume-once@example.test",
            keywords={},
        )
        self.email_state = "email-state-2"
        self.query_state = "query-state-2"
        self.mailbox_state = "mailbox-state-2"

    def send(self, request: JmapHttpRequest) -> JmapHttpResponse:
        if request.method == "GET":
            return self._response(request, self._session())
        payload = json.loads((request.body or b"{}").decode("utf-8"))
        responses: list[list[Any]] = []
        for method, arguments, call_id in payload.get("methodCalls", []):
            method = str(method)
            self.calls.append(method)
            if method == "Email/changes" and self.mode == "cannot-calculate":
                responses.append(
                    [
                        "error",
                        {"type": "cannotCalculateChanges"},
                        call_id,
                    ]
                )
            else:
                responses.append(
                    [method, self._method(method, dict(arguments)), call_id]
                )
        return self._response(request, {"methodResponses": responses})

    def _method(self, method: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if method == "Email/changes":
            since = str(arguments.get("sinceState") or "")
            if self.mode == "paged":
                if since == "email-state-1":
                    return self._changes(
                        old=since,
                        new="email-state-2",
                        created=("E3",),
                        has_more=True,
                    )
                if since == "email-state-2":
                    return self._changes(
                        old=since,
                        new="email-state-3",
                        updated=("E1",),
                    )
            if self.mode == "single" and since == "email-state-1":
                return self._changes(
                    old=since,
                    new="email-state-2",
                    created=("E3",),
                )
            return self._changes(old=since, new=self.email_state)
        if method == "Email/queryChanges":
            since = str(arguments.get("sinceQueryState") or "")
            if self.mode == "paged":
                if since == "query-state-1":
                    return self._query_changes(
                        old=since,
                        new="query-state-2",
                        added=("E3",),
                        has_more=True,
                    )
                if since == "query-state-2":
                    return self._query_changes(
                        old=since,
                        new="query-state-3",
                    )
            if self.mode == "single" and since == "query-state-1":
                return self._query_changes(
                    old=since,
                    new="query-state-2",
                    added=("E3",),
                )
            return self._query_changes(old=since, new=self.query_state)
        if method == "Mailbox/changes":
            return {
                "accountId": "A1",
                "oldState": str(arguments.get("sinceState") or ""),
                "newState": self.mailbox_state,
                "hasMoreChanges": False,
                "created": [],
                "updated": [],
                "destroyed": [],
            }
        return super()._method(method, arguments)

    @staticmethod
    def _changes(
        *,
        old: str,
        new: str,
        created: tuple[str, ...] = (),
        updated: tuple[str, ...] = (),
        destroyed: tuple[str, ...] = (),
        has_more: bool = False,
    ) -> dict[str, Any]:
        return {
            "accountId": "A1",
            "oldState": old,
            "newState": new,
            "hasMoreChanges": has_more,
            "created": list(created),
            "updated": list(updated),
            "destroyed": list(destroyed),
        }

    @staticmethod
    def _query_changes(
        *,
        old: str,
        new: str,
        added: tuple[str, ...] = (),
        removed: tuple[str, ...] = (),
        has_more: bool = False,
    ) -> dict[str, Any]:
        return {
            "accountId": "A1",
            "oldQueryState": old,
            "newQueryState": new,
            "hasMoreChanges": has_more,
            "added": [
                {"id": email_id, "index": index}
                for index, email_id in enumerate(added)
            ],
            "removed": list(removed),
            "total": 0,
        }


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    active: dict[str, StateFailureJmapAdapter],
) -> None:
    def transport(**kwargs: Any) -> JmapHttpTransport:
        return JmapHttpTransport(
            endpoint_policy=kwargs["endpoint_policy"],
            adapter=active["adapter"],
            limits=kwargs["limits"],
            observer=kwargs["observer"],
        )

    monkeypatch.setattr(mail_task_composition, "JmapHttpTransport", transport)


def _harness(
    root: Path,
    intent_hub: InProcessIntentHub,
    *,
    fault=None,
) -> AnantaMailCompositionHarness:
    return AnantaMailCompositionHarness(
        data_root=root / "mail",
        secret_root=SECRET_ROOT,
        password_file=PASSWORD_FILE,
        intent_hub=intent_hub,
        sync_fault_injector=fault,
    )


def _checkpoint(database_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            """
            SELECT mailbox_state, email_state, query_state, revision
              FROM mail_sync_checkpoints
             WHERE account_id = 'jmap-fixture-alice'
               AND provider_account_id = 'A1'
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AssertionError("sync checkpoint missing")
    return {
        "mailbox_state": row[0],
        "email_state": row[1],
        "query_state": row[2],
        "revision": row[3],
    }


def _provider_ids(harness: AnantaMailCompositionHarness) -> list[str]:
    return sorted(
        str(dict(row["message_ref"]["protocol_locator"]).get("email_id") or "")
        for row in harness.metadata.list_messages(account_id="jmap-fixture-alice")
    )


def test_paginated_changes_then_bounded_cannot_calculate_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = StateFailureJmapAdapter()
    active = {"adapter": adapter}
    _install_transport(monkeypatch, active)

    with InProcessIntentHub() as intent_hub:
        harness = _harness(tmp_path, intent_hub)
        baseline = harness.run("sync")
        assert baseline["status"] == "completed"

        adapter.configure_paged_changes()
        marker = len(adapter.calls)
        paged = harness.run("sync")
        paged_calls = adapter.calls[marker:]

        assert paged["status"] == "completed"
        assert paged["counters"] == {
            "created_count": 1,
            "updated_count": 1,
            "destroyed_count": 0,
            "rebuild_count": 0,
        }
        assert paged_calls.count("Email/changes") == 2
        assert paged_calls.count("Email/queryChanges") == 2
        assert _provider_ids(harness) == ["E1", "E2", "E3"]
        assert _checkpoint(
            harness.data_root / "runtime-state-v1.sqlite3"
        ) == {
            "mailbox_state": "mailbox-state-2",
            "email_state": "email-state-3",
            "query_state": "query-state-3",
            "revision": 2,
        }

        adapter.configure_rebuild()
        marker = len(adapter.calls)
        rebuilt = harness.run("sync")
        rebuild_calls = adapter.calls[marker:]

    assert rebuilt["status"] == "completed"
    assert rebuild_calls.count("Email/changes") == 1
    assert rebuild_calls.count("Email/query") == 1
    assert rebuild_calls.count("Email/get") == 2
    assert rebuild_calls.count("Mailbox/get") == 1
    assert "Email/queryChanges" not in rebuild_calls
    assert _provider_ids(harness) == ["E1", "E3", "E4"]
    assert _checkpoint(harness.data_root / "runtime-state-v1.sqlite3") == {
        "mailbox_state": "mailbox-state-3",
        "email_state": "email-state-4",
        "query_state": "query-state-4",
        "revision": 3,
    }
    assert_reference_only_results(
        [baseline, paged, rebuilt],
        forbidden_values=(
            PASSWORD_FILE.read_text(encoding="utf-8").strip(),
            "Rebuild replacement",
            "Paged change",
        ),
    )


def test_crash_rolls_back_and_restart_resumes_without_duplicate_or_state_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = StateFailureJmapAdapter()
    active = {"adapter": adapter}
    _install_transport(monkeypatch, active)

    with InProcessIntentHub() as intent_hub:
        baseline_harness = _harness(tmp_path, intent_hub)
        baseline = baseline_harness.run("sync")
        adapter.configure_single_change()
        injected = {"done": False}

        def crash_after_metadata(stage: str) -> None:
            if stage == "after_metadata" and not injected["done"]:
                injected["done"] = True
                raise RuntimeError("simulated_process_loss")

        crashing_harness = _harness(
            tmp_path,
            intent_hub,
            fault=crash_after_metadata,
        )
        crashed = crashing_harness.run("sync")

        assert crashed["status"] == "failed"
        assert crashed["reason_code"] == "mail_metadata_apply_failed"
        assert _provider_ids(crashing_harness) == ["E1", "E2"]
        assert _checkpoint(
            crashing_harness.data_root / "runtime-state-v1.sqlite3"
        )["revision"] == 1

        resumed_harness = _harness(tmp_path, intent_hub)
        resumed = resumed_harness.run("sync")
        steady = resumed_harness.run("sync")

    assert resumed["status"] == "completed"
    assert resumed["counters"]["created_count"] == 1
    assert _provider_ids(resumed_harness) == ["E1", "E2", "E3"]
    assert len(set(_provider_ids(resumed_harness))) == 3
    assert steady["status"] == "completed"
    assert steady["counters"] == {
        "created_count": 0,
        "updated_count": 0,
        "destroyed_count": 0,
        "rebuild_count": 0,
    }
    assert _checkpoint(
        resumed_harness.data_root / "runtime-state-v1.sqlite3"
    ) == {
        "mailbox_state": "mailbox-state-2",
        "email_state": "email-state-2",
        "query_state": "query-state-2",
        "revision": 3,
    }
    assert_reference_only_results(
        [baseline, crashed, resumed, steady],
        forbidden_values=(
            PASSWORD_FILE.read_text(encoding="utf-8").strip(),
            "Resume exactly once",
        ),
    )


def test_cas_conflict_fails_without_partial_metadata_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = StateFailureJmapAdapter()
    active = {"adapter": adapter}
    _install_transport(monkeypatch, active)

    with InProcessIntentHub() as intent_hub:
        baseline_harness = _harness(tmp_path, intent_hub)
        baseline = baseline_harness.run("sync")
        assert baseline["status"] == "completed"
        adapter.configure_single_change()
        database_path = baseline_harness.data_root / "runtime-state-v1.sqlite3"
        injected = {"done": False}

        def win_concurrent_cas(stage: str) -> None:
            if stage != "before_begin" or injected["done"]:
                return
            injected["done"] = True
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    UPDATE mail_sync_checkpoints
                       SET revision = revision + 1
                     WHERE account_id = 'jmap-fixture-alice'
                       AND provider_account_id = 'A1'
                    """
                )
                connection.commit()
            finally:
                connection.close()

        conflicting_harness = _harness(
            tmp_path,
            intent_hub,
            fault=win_concurrent_cas,
        )
        conflicted = conflicting_harness.run("sync")

    assert conflicted["status"] == "failed"
    assert conflicted["reason_code"] == "mail_sync_concurrent_update"
    assert conflicted["retryable"] is True
    assert _provider_ids(conflicting_harness) == ["E1", "E2"]
    checkpoint = _checkpoint(
        conflicting_harness.data_root / "runtime-state-v1.sqlite3"
    )
    assert checkpoint["revision"] == 2
    assert checkpoint["email_state"] == "email-state-1"
    assert_reference_only_results(
        [conflicted],
        forbidden_values=(
            PASSWORD_FILE.read_text(encoding="utf-8").strip(),
            "Resume exactly once",
        ),
    )
