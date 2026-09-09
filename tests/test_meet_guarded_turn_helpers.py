"""Closed test infrastructure and relay adapter seams; no Docker or live grants."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_dialog_egress_guard import namespace_info, session_policy
from tests.meet_dialog_worker_container import DialogWorkerContainer
from tests.meet_multi_worker_guarded_turn import MultiWorkerGuardedTurn
from tests.meet_worker_relay_context import install_relay_context, relay_context_script
from tests.test_meet_dialog_worker_container_fixture import HUB, IMAGE, NETWORK

ORIGIN, TURN = "https://172.30.0.2", "turn:172.30.0.4:3478?transport=udp"


def test_session_ceiling_has_only_three_exact_endpoints_and_hub_caller():
    value = session_policy(HUB, ORIGIN, TURN).projection()
    assert value["endpoints"] == [
        {"address": "172.30.0.1", "protocol": "tcp", "port": 12345},
        {"address": "172.30.0.2", "protocol": "tcp", "port": 443},
        {"address": "172.30.0.4", "protocol": "udp", "port": 3478},
    ]
    assert value["hub_clients"] == ["172.30.0.1"] and value["names"] == []


@pytest.mark.parametrize(
    "hub,origin,turn",
    [
        (HUB + "?allow=all", ORIGIN, TURN),
        (HUB, ORIGIN + "/machine", TURN),
        (HUB, ORIGIN.replace("https", "http"), TURN),
        (HUB, ORIGIN, TURN + "&peer=all"),
        (HUB, ORIGIN, TURN.replace("3478", "3479")),
        (HUB, ORIGIN, TURN.replace("udp", "tls")),
        (HUB, ORIGIN, TURN.replace("172.30.0.4", "8.8.8.8")),
        (HUB, ORIGIN, TURN.replace("172.30.0.4", "172.30.0.2")),
    ],
)
def test_session_scope_does_not_guess_or_expand_endpoints(hub, origin, turn):
    with pytest.raises(ValueError):
        session_policy(hub, origin, turn)


def guard_row():
    return {
        "Id": "b" * 64,
        "Name": "/meet-test-egress-11111111-1111-1111-1111-111111111111",
        "HostConfig": {"NetworkMode": NETWORK, "Privileged": False, "PidMode": ""},
        "NetworkSettings": {"Networks": {NETWORK: {"IPAddress": "172.30.0.8"}}},
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "Config": {"Labels": {"ananta.meet-test-guard": "fixed-endpoints-v1"}},
    }


@pytest.mark.parametrize("mutation", ["id", "host", "network", "pid", "privileged", "label", "stopped", "health"])
def test_unowned_or_unhealthy_namespace_is_not_shared(mutation):
    value = guard_row()
    if mutation == "id":
        value["Id"] = "c" * 64
    elif mutation == "host":
        value["HostConfig"]["NetworkMode"] = "host"
    elif mutation == "network":
        value["NetworkSettings"]["Networks"] = {"serving-network": {}}
    elif mutation == "pid":
        value["HostConfig"]["PidMode"] = "host"
    elif mutation == "privileged":
        value["HostConfig"]["Privileged"] = True
    elif mutation == "label":
        value["Config"]["Labels"] = {}
    elif mutation == "stopped":
        value["State"]["Running"] = False
    else:
        value["State"]["Health"]["Status"] = "starting"
    with pytest.raises(ValueError, match="namespace_invalid"):
        namespace_info(Mock(return_value=json.dumps([value])), "b" * 64, NETWORK)


def test_namespace_admission_preserves_exact_guard_projection():
    value = guard_row()
    assert namespace_info(Mock(return_value=json.dumps([value])), "b" * 64, NETWORK) == value


@pytest.mark.parametrize(
    "options",
    [
        {"network_namespace": "host"},
        {"network_namespace": True},
        {"relay_url": TURN},
        {"network_namespace": "b" * 64, "relay_url": "turn:public:3478"},
    ],
)
def test_bad_worker_namespace_or_relay_configuration_cannot_call_docker(options):
    command = Mock()
    with pytest.raises(ValueError):
        DialogWorkerContainer(NETWORK, IMAGE, HUB, command=command, **options)
    command.assert_not_called()


def test_relay_script_is_the_existing_authorized_session_adapter_not_new_credentials():
    path = Path(__file__).resolve().parents[2] / "webrtc-minimize-server/test/helpers/machine-forced-relay.js"
    script = relay_context_script(TURN, path)
    assert script.startswith("(function installMachineForcedRelay(")
    assert "nativeFetch(...args)" in script and "authorizedServers" in script
    assert script.endswith(")(" + json.dumps(TURN) + ");")


def test_relay_adapter_preserves_context_and_closes_failed_installation(tmp_path):
    path = tmp_path / "relay.js"
    path.write_text("export function installMachineForcedRelay(url) { return url; }")
    created = Mock()

    class Browser:
        def new_context(self, **options):
            assert options == {"permissions": []}
            return created

    install_relay_context(Browser, TURN, path)
    assert Browser().new_context(permissions=[]) is created
    created.add_init_script.assert_called_once()
    created.close.assert_not_called()
    created.add_init_script.side_effect = RuntimeError("test-install-failure")
    with pytest.raises(RuntimeError):
        Browser().new_context(permissions=[])
    created.close.assert_called_once()


def test_relay_console_observer_retains_only_eight_fixed_numeric_codes(tmp_path, monkeypatch):
    path = tmp_path / "relay.js"
    path.write_text("export function installMachineForcedRelay(url) { return url; }")
    created = Mock()

    class Browser:
        def new_context(self):
            return created

    install_relay_context(Browser, TURN, path)
    Browser().new_context()
    page = Mock()
    created.on.call_args.args[1](page)
    callback = page.on.call_args.args[1]
    destination = Mock()

    def diagnostic_path(value):
        assert value == "/state/relay-diagnostic.json"
        return destination

    monkeypatch.setattr("tests.meet_worker_relay_context.Path", diagnostic_path)
    for text in [
        "private secret",
        "test_relay_ice_error:1000",
        "test_relay_ice_error:486 private",
        *["test_relay_ice_error:486"] * 20,
    ]:
        callback(SimpleNamespace(text=text))
    assert destination.write_text.call_count == 8
    assert json.loads(destination.write_text.call_args.args[0]) == [486] * 8


def test_default_scenario_keeps_original_bridge_shape_and_has_no_guard_work():
    scenario = MultiWorkerGuardedTurn(False)
    assert scenario.environment == {"MEET_MULTI_WORKER_ICE_PATH": "direct"}
    assert not scenario.mounts and scenario.worker_options({}, None, None, None) == {}
    screen = {"moving": [True, True], "departedAbsent": False}
    assert scenario.screen_observation(screen, 2) is screen
    scenario.require_ready(dict.fromkeys(["origin", "room_id", "certificate", "test_network"]))
    with pytest.raises(AssertionError):
        scenario.require_ready({"unexpected": True})


def test_guarded_scenario_requires_explicit_transport_and_observed_relay_pairs():
    scenario = MultiWorkerGuardedTurn("guarded-turn-udp")
    ready = dict.fromkeys(["origin", "room_id", "certificate", "test_network", "turn_url"])
    ready["ice_path"] = "turn-udp"
    scenario.require_ready(ready)
    with pytest.raises(AssertionError):
        scenario.require_ready(ready | {"ice_path": "turn-tcp"})
    value = {
        "moving": [True, True],
        "departedAbsent": False,
        "relay": {
            "connections": 2,
            "sctpConnections": 2,
            "pairs": 2,
            "relayPairs": 2,
            "policyFailures": 0,
            "sent": 1,
            "received": 1,
        },
    }
    assert scenario.screen_observation(value, 2) == {"moving": [True, True], "departedAbsent": False}
    wrong = deepcopy(value)
    wrong["relay"]["relayPairs"] = 1
    with pytest.raises(AssertionError):
        scenario.screen_observation(wrong, 2)


@pytest.mark.parametrize("mode,path", [("guarded-auto-udp", "turn-udp"), ("guarded-auto-tcp", "turn-tcp")])
def test_automatic_worker_fallback_has_no_forced_relay_source_mount(mode, path):
    scenario = MultiWorkerGuardedTurn(mode)
    assert scenario.enabled and scenario.path == path
    assert not scenario.force_from_start and scenario.mounts == set()
