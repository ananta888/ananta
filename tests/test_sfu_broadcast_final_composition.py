from __future__ import annotations

from flask import Blueprint, Flask

import agent.bootstrap.sfu_broadcast_final_composition as composition
from agent.repositories.sfu_hub_control_repository import (
    SqlSfuBroadcastCommandLedger,
    SqlSfuBroadcastOperationsSnapshotRepository,
    SqlSfuFanoutReconciliationControlRepository,
    SqlSfuScopeEpochResolver,
)
from agent.repositories.turn_observation_cursor_repository import (
    SqlTurnObservationCursorRepository,
)
from agent.repositories.turn_observer_identity_repository import (
    SqlTurnObserverIdentityRepository,
)
from agent.repositories.turn_pool_repository import SqlTurnPoolRepository
from agent.repositories.sfu_fleet_reconciliation_repository import (
    SqlSfuRouteReconciliationScopeRepository,
)


def test_final_composition_wires_only_durable_local_control(monkeypatch) -> None:
    app = Flask(__name__)
    app.secret_key = "s" * 64
    monkeypatch.setattr(
        composition,
        "initialize_sfu_broadcast_hub_composition",
        lambda _app: {},
    )

    status = composition.initialize_sfu_broadcast_final_composition(app)

    assert status["operations_read_model_ready"] is True
    assert status["command_ledger_ready"] is True
    assert status["command_service_ready"] is False
    assert status["capability_scope_ready"] is True
    assert status["layer_projection_scope_ready"] is True
    assert status["route_reconciliation_control_ready"] is True
    assert status["route_reconciliation_service_ready"] is False
    assert status["route_reconciliation_scope_ready"] is True
    assert status["fleet_reconciliation_ports_ready"] is False
    assert status["fleet_reconciliation_job_ready"] is False
    assert status["route_reconciliation_job_ready"] is False
    assert status["livekit_observation_ready"] is False
    assert status["member_digest_kms_ready"] is False
    assert status["turn_ca_policy_ready"] is False
    assert app.extensions["sfu_broadcast_extension_blueprints"] == ()
    assert isinstance(
        app.extensions["sfu_broadcast_operations_snapshot_port"],
        SqlSfuBroadcastOperationsSnapshotRepository,
    )
    assert isinstance(
        app.extensions["sfu_broadcast_command_ledger"],
        SqlSfuBroadcastCommandLedger,
    )
    assert isinstance(
        app.extensions["sfu_scope_epoch_resolver"],
        SqlSfuScopeEpochResolver,
    )
    assert isinstance(
        app.extensions["sfu_fanout_route_reconciliation_control_repository"],
        SqlSfuFanoutReconciliationControlRepository,
    )
    assert isinstance(
        app.extensions["turn_observer_identity_repository"],
        SqlTurnObserverIdentityRepository,
    )
    assert isinstance(
        app.extensions["turn_observation_cursor_repository"],
        SqlTurnObservationCursorRepository,
    )
    assert isinstance(app.extensions["turn_pool_repository"], SqlTurnPoolRepository)
    assert isinstance(
        app.extensions["sfu_route_reconciliation_scope_page_port"],
        SqlSfuRouteReconciliationScopeRepository,
    )
    assert "sfu_fleet_reconciliation_job" not in app.extensions
    assert "sfu_fanout_route_reconciler_job" not in app.extensions


def test_extension_blueprints_are_registered_exactly_once() -> None:
    app = Flask(__name__)
    blueprint = Blueprint("dependency_built", __name__)

    @blueprint.get("/dependency-built")
    def dependency_built() -> str:
        return "ok"

    app.extensions["sfu_broadcast_extension_blueprints"] = (blueprint, blueprint)

    composition.register_sfu_broadcast_extension_blueprints(app)
    composition.register_sfu_broadcast_extension_blueprints(app)

    assert tuple(app.blueprints).count("dependency_built") == 1
    assert sum(rule.rule == "/dependency-built" for rule in app.url_map.iter_rules()) == 1


def test_short_application_secret_keeps_secret_dependent_graphs_closed(
    monkeypatch,
) -> None:
    app = Flask(__name__)
    app.secret_key = "short"
    monkeypatch.setattr(
        composition,
        "initialize_sfu_broadcast_hub_composition",
        lambda _app: {},
    )

    status = composition.initialize_sfu_broadcast_final_composition(app)

    assert status["operations_read_model_ready"] is False
    assert status["capability_scope_ready"] is False
    assert status["route_reconciliation_control_ready"] is False
    assert status["member_digest_kms_ready"] is False
