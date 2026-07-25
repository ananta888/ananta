"""Opt-in smoke against an operator-supplied, vendor-neutral JMAP account."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.jmap_live_provider_support import (
    LIVE_RUN_ENV,
    LiveJmapProviderConfig,
    keyword_membership,
    write_live_evidence,
)
from tests.e2e.mail_ananta_composition_harness import (
    AnantaMailCompositionHarness,
    InProcessIntentHub,
    assert_reference_only_results,
)


ROOT = Path(__file__).resolve().parents[2]
SMOKE_KEYWORD = "ananta-provider-smoke"

pytestmark = pytest.mark.skipif(
    os.environ.get(LIVE_RUN_ENV) != "1",
    reason="external JMAP provider smoke requires explicit opt-in",
)


def _mutation_payload(
    *,
    message_ref: Any,
    state: str,
    add: bool,
    suffix: str,
) -> dict[str, Any]:
    return {
        "message_refs": [message_ref.to_dict()],
        "action": "set_keywords",
        "add_keywords": [SMOKE_KEYWORD] if add else [],
        "remove_keywords": [] if add else [SMOKE_KEYWORD],
        "if_in_state": state,
        "intent_ref": f"mutation-live-{suffix}",
        "audit_ref": f"audit-mutation-live-{suffix}",
    }


def test_external_jmap_provider_smoke(tmp_path: Path) -> None:
    config = LiveJmapProviderConfig.from_environment(
        repo_root=ROOT,
    )
    results: dict[str, dict[str, Any]] = {}
    changed = False
    restore_add = False

    with InProcessIntentHub() as intent_hub:
        harness = AnantaMailCompositionHarness(
            data_root=tmp_path / "mail",
            secret_root=tmp_path,
            password_file=None,
            intent_hub=intent_hub,
            session_url=config.session_url,
            account_id="jmap-live-provider",
            display_name="JMAP Live Provider",
            username_ref="env://ANANTA_JMAP_LIVE_USERNAME",
            credential_ref="env://ANANTA_JMAP_LIVE_CREDENTIAL",
            auth_mode=config.auth_mode,
            provider_account_id=config.provider_account_id,
            runtime_environment=config.runtime_environment(),
            external_network_enabled=True,
            local_endpoints_enabled=False,
            workspace_id="jmap-live-provider-smoke",
            actor="jmap-live-provider-smoke",
        )
        results["discovery"] = harness.run("discovery")
        results["sync"] = harness.run("sync")
        message_ref, metadata = harness.first_message()
        results["body"] = harness.run(
            "body",
            intent_payload={
                "message_ref": message_ref.to_dict(),
                "release_scope": "full_body",
            },
        )
        restore_add = keyword_membership(metadata, SMOKE_KEYWORD)
        try:
            results["mutation"] = harness.run(
                "mutation",
                intent_payload=_mutation_payload(
                    message_ref=message_ref,
                    state=harness.email_state(),
                    add=not restore_add,
                    suffix="apply",
                ),
            )
            changed = results["mutation"].get("status") == "completed"
            if not changed:
                raise AssertionError("jmap_live_mutation_failed")
        finally:
            if changed:
                results["resync"] = harness.run("sync")
                results["restore"] = harness.run(
                    "mutation",
                    intent_payload=_mutation_payload(
                        message_ref=message_ref,
                        state=harness.email_state(),
                        add=restore_add,
                        suffix="restore",
                    ),
                )
                if results["restore"].get("status") != "completed":
                    raise AssertionError("jmap_live_mutation_restore_failed")
                results["final_sync"] = harness.run("sync")

    assert all(result.get("status") == "completed" for result in results.values())
    assert_reference_only_results(
        list(results.values()),
        forbidden_values=(config.credential,),
    )
    write_live_evidence(config, results=results)
