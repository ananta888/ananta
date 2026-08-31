from __future__ import annotations

import pytest

from agent.services.business_controlling_runtime_control import (
    BusinessControllingRuntimeControlError,
    BusinessControllingRuntimeControlService,
    JsonBusinessControllingRuntimeControlRepository,
)


def test_default_is_disabled_and_cas_update_survives_restart(tmp_path) -> None:
    path = tmp_path / "controlling-runtime.json"
    repository = JsonBusinessControllingRuntimeControlRepository(path)
    assert repository.snapshot().global_enabled is False

    state = BusinessControllingRuntimeControlService(repository).replace(
        expected_revision=0,
        global_enabled=True,
        statistical_enabled=True,
        explanations_enabled=True,
        disabled_catalog_entry_ids=("skillentry_" + "a" * 64,),
        actor_id="operator-a",
        reason="synthetic-pilot",
    )

    restored = JsonBusinessControllingRuntimeControlRepository(path).snapshot()
    assert restored == state
    assert restored.catalog_entry_enabled("skillentry_" + "a" * 64) is False
    assert restored.catalog_entry_enabled("skillentry_" + "b" * 64) is True


def test_stale_update_and_tampered_state_fail_closed(tmp_path) -> None:
    path = tmp_path / "controlling-runtime.json"
    repository = JsonBusinessControllingRuntimeControlRepository(path)
    service = BusinessControllingRuntimeControlService(repository)
    service.replace(
        expected_revision=0,
        global_enabled=True,
        statistical_enabled=False,
        explanations_enabled=True,
        disabled_catalog_entry_ids=(),
        actor_id="operator-a",
        reason="rules-only",
    )
    with pytest.raises(BusinessControllingRuntimeControlError, match="revision_conflict"):
        service.replace(
            expected_revision=0,
            global_enabled=False,
            statistical_enabled=False,
            explanations_enabled=False,
            disabled_catalog_entry_ids=(),
            actor_id="operator-a",
            reason="stale",
        )

    path.write_text(path.read_text().replace('"global_enabled": true', '"global_enabled": false'))
    with pytest.raises(BusinessControllingRuntimeControlError, match="digest_invalid"):
        repository.snapshot()
