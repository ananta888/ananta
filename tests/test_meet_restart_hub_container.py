"""Destructive restart operations require the exact test-owned container."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_restart_hub_container import RestartHubContainer


def fixture():
    hub = object.__new__(RestartHubContainer)
    hub.identifier = "a" * 64
    hub.image = "sha256:" + "b" * 64
    hub.name = "meet-test-restart-hub-" + "c" * 36
    hub.network = "meet-test-tls-" + "d" * 36 + "-network"
    row = {
        "Id": hub.identifier,
        "Name": "/" + hub.name,
        "Image": hub.image,
        "NetworkSettings": {"Networks": {hub.network: {}}},
        "State": {"Running": True, "Pid": 123},
    }
    hub.command = Mock(return_value=json.dumps([row]))
    return hub, row


@pytest.mark.parametrize("field", ["Id", "Name", "Image", "NetworkSettings"])
def test_foreign_or_changed_container_is_never_killed(field):
    hub, row = fixture()
    row[field] = {"Networks": {"foreign": {}}} if field == "NetworkSettings" else "foreign"
    hub.command.return_value = json.dumps([row])
    with pytest.raises(ValueError, match="owned_container_mismatch"):
        hub.kill()
    assert hub.command.call_args_list == [(("inspect", hub.name),)]


def test_verified_kill_and_restart_are_bound_to_original_container_id():
    hub, row = fixture()
    stopped = deepcopy(row)
    stopped["State"] = {"Running": False, "ExitCode": 137, "Pid": 0}
    hub.command.side_effect = [json.dumps([row]), "", json.dumps([stopped])]
    assert hub.kill() == 123
    assert hub.command.call_args_list[1].args == ("kill", "--signal=KILL", hub.identifier)
    row["State"]["Pid"] = 456
    hub.command.reset_mock(side_effect=True)
    hub.command.side_effect = [json.dumps([stopped]), "", json.dumps([row])]
    hub.wait_ready = Mock()
    hub.restart(123)
    assert hub.command.call_args_list[1].args == ("start", hub.identifier)
    hub.wait_ready.assert_called_once_with()


def test_running_container_cannot_be_restarted_as_if_it_had_crashed():
    hub, _ = fixture()
    with pytest.raises(ValueError, match="restart_transition_invalid"):
        hub.restart(123)
    assert hub.command.call_count == 1


@pytest.mark.parametrize("image", [None, "latest", "repo:tag", "sha256:short"])
def test_image_identity_is_rejected_before_any_directory_or_port_allocation(tmp_path, image):
    with pytest.raises(ValueError, match="image_invalid"):
        RestartHubContainer(SimpleNamespace(), image, tmp_path)
    assert not (tmp_path / "restart-hub").exists()
