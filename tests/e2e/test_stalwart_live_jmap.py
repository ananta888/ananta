from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.stalwart_jmap_test_stack import (
    LIVE_ACK_ENV,
    LIVE_RUN_ENV,
    ComposeStack,
    FixturePlan,
    FixtureSecrets,
    HttpJmapTransport,
    StalwartFixtureSeeder,
)
from tests.e2e.mail_ananta_composition_harness import (
    AnantaMailCompositionHarness,
    InProcessIntentHub,
    assert_reference_only_results,
    run_fixture_migration_and_restore,
)


pytestmark = pytest.mark.skipif(
    os.environ.get(LIVE_RUN_ENV) != "1" or os.environ.get(LIVE_ACK_ENV) != "1",
    reason="live Stalwart E2E requires run opt-in and organizational license acknowledgement",
)


def test_seeded_stalwart_jmap_stack(tmp_path: Path) -> None:
    plan = FixturePlan.load()
    secrets = FixtureSecrets.load(plan)
    stack = ComposeStack(secrets.admin)
    try:
        stack.reset()
        result = StalwartFixtureSeeder(
            plan,
            secrets,
            HttpJmapTransport(),
        ).seed()
        assert result["account_count"] == 2
        assert result["thread_groups"] == {"lighthouse": 2}
        assert result["attachment_count"] == 1
        secret_root = plan.path("tests/fixtures/jmap/secrets")
        with InProcessIntentHub() as intent_hub:
            harness = AnantaMailCompositionHarness(
                data_root=tmp_path / "mail",
                secret_root=secret_root,
                password_file=secret_root / "alice-password",
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
                    "intent_ref": "mutation-live",
                    "audit_ref": "audit-mutation-live",
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
        assert all(
            item["status"] == "completed"
            for item in (discovery, sync, body, mutation)
        )
        assert unresolved["reason_code"] == "mail_protocol_unresolved"
        assert imap_fallback["provider"] == "imap"
        migration = run_fixture_migration_and_restore(tmp_path / "migration")
        assert migration["restore_status"] == "restored"
        assert_reference_only_results(
            [
                discovery,
                sync,
                body,
                mutation,
                unresolved,
                imap_fallback,
                migration,
            ],
            forbidden_values=(
                secrets.admin,
                *secrets.accounts.values(),
                "The first deterministic fixture message.",
            ),
        )
    finally:
        stack.down()
