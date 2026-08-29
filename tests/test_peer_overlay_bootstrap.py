from flask import Flask

from agent.bootstrap.peer_overlay import initialize_peer_overlay


def test_peer_overlay_is_hub_owned_and_worker_fails_closed(tmp_path) -> None:
    hub = Flask("hub")
    hub.secret_key = "test-secret"
    hub.config.update(ROLE="hub", ANANTA_PEER_OVERLAY_STATE=str(tmp_path / "overlay.sqlite3"))
    status = initialize_peer_overlay(hub)
    assert status.ready is True
    assert status.media_peer_dag == "no_go"
    assert "peer_overlay_control_service" in hub.extensions

    worker = Flask("worker")
    worker.secret_key = "test-secret"
    worker.config["ROLE"] = "worker"
    status = initialize_peer_overlay(worker)
    assert status.ready is False
    assert status.reason_code == "peer_overlay_hub_role_required"
    assert "peer_overlay_control_service" not in worker.extensions


def test_peer_overlay_data_path_is_default_off(tmp_path) -> None:
    hub = Flask("hub-default-off")
    hub.secret_key = "test-secret"
    hub.config.update(ROLE="hub", ANANTA_PEER_OVERLAY_STATE=str(tmp_path / "overlay.sqlite3"))
    initialize_peer_overlay(hub)
    assert hub.extensions["peer_overlay_control_service"].overview()["data_overlay"] == "disabled"
