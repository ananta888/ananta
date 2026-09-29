"""Abrupt crash injection must never target an unowned or changed container."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_multi_worker_crash import crash_owned_worker


def fixture():
    container = SimpleNamespace(
        created=True,
        name="meet-test-dialog-worker-00000000-0000-0000-0000-000000000000",
        image="sha256:" + "a" * 64,
        network="private-fixture-network",
    )
    row = {
        "Id": "b" * 64,
        "Name": "/" + container.name,
        "Image": container.image,
        "State": {"Running": True},
        "NetworkSettings": {"Networks": {container.network: {}}},
    }
    return container, row


def test_crash_resolves_owned_target_then_uses_only_immutable_id():
    container, row = fixture()
    container.command = Mock(side_effect=[json.dumps([row]), "", '{"Running":false,"ExitCode":137}'])
    crash_owned_worker(container)
    assert container.command.call_args_list[1].args == ("kill", "--signal=KILL", row["Id"])
    assert container.command.call_args_list[2].args[1] == row["Id"]
    assert container.created  # Normal cleanup must still remove this exact fixture.


@pytest.mark.parametrize("field", ["created", "name", "Id", "Name", "Image", "State", "NetworkSettings"])
def test_changed_or_unowned_target_never_receives_a_kill(field):
    container, row = fixture()
    if field == "created":
        container.created = False
    elif field == "name":
        container.name = "existing-service"
    else:
        row[field] = {} if field in {"State", "NetworkSettings"} else "foreign"
    container.command = Mock(return_value=json.dumps([row]))
    with pytest.raises(ValueError, match="target_invalid"):
        crash_owned_worker(container)
    assert all(call.args[0] == "inspect" for call in container.command.call_args_list)


def test_graceful_or_still_running_exit_does_not_count_as_crash():
    container, row = fixture()
    container.command = Mock(side_effect=[json.dumps([row]), "", '{"Running":false,"ExitCode":0}'])
    with pytest.raises(AssertionError, match="exit abruptly"):
        crash_owned_worker(container)
