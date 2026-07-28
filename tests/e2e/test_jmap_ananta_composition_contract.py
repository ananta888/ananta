from __future__ import annotations

# Uses only the repository-owned, provider-neutral JMAP contract adapter.

from pathlib import Path

import pytest

from agent.services.jmap_http_transport import JmapHttpTransport
from tests.e2e.mail_ananta_composition_harness import (
    AnantaMailCompositionHarness,
    ContractJmapAdapter,
    InProcessIntentHub,
    assert_reference_only_results,
    run_fixture_migration_and_restore,
)
from worker import mail_task_composition


ROOT = Path(__file__).resolve().parents[2]


def test_real_hub_and_production_provider_composition_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ContractJmapAdapter()

    def transport(**kwargs):
        return JmapHttpTransport(
            endpoint_policy=kwargs["endpoint_policy"],
            adapter=adapter,
            limits=kwargs["limits"],
            observer=kwargs["observer"],
        )

    monkeypatch.setattr(mail_task_composition, "JmapHttpTransport", transport)
    secret_root = ROOT / "tests/fixtures/jmap/secrets"
    password_file = secret_root / "alice-password"
    with InProcessIntentHub() as intent_hub:
        harness = AnantaMailCompositionHarness(
            data_root=tmp_path / "mail",
            secret_root=secret_root,
            password_file=password_file,
            intent_hub=intent_hub,
        )
        discovery = harness.run("discovery")
        sync = harness.run("sync")
        message_ref = harness.message_ref(subject="Project lighthouse")
        body = harness.run(
            "body",
            intent_payload={
                "message_ref": message_ref.to_dict(),
                "release_scope": "full_body",
            },
        )
        mutation = harness.run(
            "mutation",
            intent_payload={
                "message_refs": [message_ref.to_dict()],
                "action": "set_keywords",
                "add_keywords": ["$flagged"],
                "remove_keywords": [],
                "if_in_state": harness.email_state(),
                "intent_ref": "mutation-contract",
                "audit_ref": "audit-mutation-contract",
            },
        )

        harness.add_auto_account(resolved_protocol=None)
        unresolved = harness.run(
            "discovery",
            account_id="auto-unresolved",
        )
        harness.add_auto_account(resolved_protocol="imap")
        imap_fallback = harness.run(
            "discovery",
            account_id="auto-imap",
        )

    assert [discovery["status"], sync["status"], body["status"], mutation["status"]] == [
        "completed",
        "completed",
        "completed",
        "completed",
    ]
    assert {item["provider"] for item in (discovery, sync, body, mutation)} == {
        "jmap"
    }
    assert sync["counters"]["created_count"] == 2
    assert body["counters"] == {"content_ref_count": 1}
    assert mutation["counters"] == {"failed_count": 0, "succeeded_count": 1}
    assert adapter.messages["E1"]["keywords"]["$flagged"] is True
    assert unresolved["reason_code"] == "mail_protocol_unresolved"
    assert unresolved["provider"] == ""
    assert imap_fallback["status"] == "failed"
    assert imap_fallback["provider"] == "imap"

    migration = run_fixture_migration_and_restore(tmp_path / "migration")
    assert migration == {
        "migration_status": "complete",
        "migration_reason_code": "ok",
        "restore_status": "restored",
        "restore_reason_code": "restore_complete",
    }
    assert_reference_only_results(
        [discovery, sync, body, mutation, unresolved, imap_fallback, migration],
        forbidden_values=(
            password_file.read_text(encoding="utf-8").strip(),
            "deterministic contract body",
            "Project lighthouse",
        ),
    )
    assert not {
        "content",
        "text_body",
        "html_body",
        "attachments",
        "credential",
    }.intersection(body)
