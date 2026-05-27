from __future__ import annotations

import pytest

from agent.services.worker_role_config_service import (
    WorkerRoleConfigError,
    WorkerRoleConfigService,
    get_default_worker_role_config,
)

SVC = WorkerRoleConfigService()


def test_defaults():
    cfg = SVC.normalize({})
    assert cfg.runtime_mode == "local"
    assert cfg.control_worker == "ananta-worker"
    assert cfg.evolution_worker == "ananta-worker"
    assert cfg.code_implementation_worker == "opencode"
    assert cfg.auto_activation is False


def test_opencode_as_control_worker_blocked():
    cfg = SVC.normalize({"control_worker": "opencode"})
    errors = SVC.validate(cfg)
    assert any("opencode_not_allowed_as_heuristic_controller" in e for e in errors)


def test_opencode_as_evolution_worker_blocked():
    cfg = SVC.normalize({"evolution_worker": "open_code"})
    errors = SVC.validate(cfg)
    assert any("opencode_not_allowed_as_heuristic_controller" in e for e in errors)


def test_auto_activation_blocked():
    cfg = SVC.normalize({"auto_activation": True})
    errors = SVC.validate(cfg)
    assert "auto_activation_must_be_false" in errors


def test_normalize_and_validate_raises_on_error():
    with pytest.raises(WorkerRoleConfigError):
        SVC.normalize_and_validate({"control_worker": "opencode"})


def test_domain_ttl_overrides():
    cfg = SVC.normalize({
        "domain_overrides": {
            "snake_tui": {"ttl_min_seconds": 2, "ttl_max_seconds": 10, "ttl_default_seconds": 5}
        }
    })
    ttl = cfg.ttl_for("snake_tui")
    assert ttl.ttl_min_seconds == 2.0
    assert ttl.ttl_max_seconds == 10.0
    assert ttl.ttl_default_seconds == 5.0


def test_ttl_default_for_unknown_domain():
    cfg = SVC.normalize({})
    ttl = cfg.ttl_for("snake_tui")
    assert ttl.ttl_default_seconds == 7.0
    ttl_chat = cfg.ttl_for("chat_codecompass")
    assert ttl_chat.ttl_default_seconds == 15.0


def test_ttl_validation_min_less_than_1():
    cfg = SVC.normalize({
        "domain_overrides": {"snake_tui": {"ttl_min_seconds": 0.5, "ttl_max_seconds": 10, "ttl_default_seconds": 2}}
    })
    errors = SVC.validate(cfg)
    assert any("ttl_out_of_range" in e for e in errors)


def test_ttl_validation_default_less_than_min():
    cfg = SVC.normalize({
        "domain_overrides": {"snake_tui": {"ttl_min_seconds": 5, "ttl_max_seconds": 30, "ttl_default_seconds": 3}}
    })
    errors = SVC.validate(cfg)
    assert any("default<min" in e for e in errors)


def test_invalid_runtime_mode_falls_back_to_local():
    cfg = SVC.normalize({"runtime_mode": "ai_only"})
    assert cfg.runtime_mode == "local"


def test_get_default_worker_role_config():
    cfg = get_default_worker_role_config()
    assert cfg.auto_activation is False
    assert cfg.control_worker == "ananta-worker"
