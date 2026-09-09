"""No scanning, inferred ownership, endpoint removal or swallowed cleanup errors."""

import json
from unittest.mock import Mock

import pytest

from tests.meet_dialog_cleanup import close_dialog_browsers
from tests.meet_dialog_network_cleanup import DialogNetworkCleanup

NAME = "meet-test-tls-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa-network"
IDENTIFIER = "a" * 64


def fixture():
    info = {"Name": NAME, "Id": IDENTIFIER, "Driver": "bridge", "Internal": True, "Containers": {}}

    def command(*args):
        if args[:2] == ("network", "inspect"):
            return json.dumps([info])
        return IDENTIFIER if args[:2] == ("network", "ls") else ""

    run = Mock(side_effect=command)
    return DialogNetworkCleanup(command=run), info, run


def test_parent_reaps_exact_pinned_network_after_bridge_exit_and_is_idempotent():
    owner, _, run = fixture()
    owner.capture(NAME)
    owner.capture(NAME)
    owner.close()
    calls = list(run.call_args_list)
    assert calls[-3].args == ("network", "ls", "--no-trunc", "--filter", "id=" + IDENTIFIER, "--format", "{{.ID}}")
    assert calls[-2].args == ("network", "inspect", IDENTIFIER)
    assert calls[-1].args == ("network", "rm", IDENTIFIER)
    owner.close()
    assert run.call_args_list == calls


@pytest.mark.parametrize("name", [None, "bridge", "host", "meet-test-tls-../foreign", "foreign-network"])
def test_unknown_scope_has_no_side_effect_or_cleanup_authority(name):
    owner, _, run = fixture()
    with pytest.raises(ValueError, match="scope_invalid"):
        owner.capture(name)
    owner.close()
    run.assert_not_called()


@pytest.mark.parametrize("change", [{"Id": "bad"}, {"Name": "foreign"}, {"Internal": False}, {"Driver": "overlay"}])
def test_invalid_network_cannot_be_adopted(change):
    owner, info, run = fixture()
    info.update(change)
    with pytest.raises(ValueError, match="identity_invalid"):
        owner.capture(NAME)
    owner.close()
    assert run.call_count == 1


@pytest.mark.parametrize("change", [{"Id": "b" * 64}, {"Name": "foreign"}, {"Internal": False},
                                    {"Driver": "overlay"}, {"Containers": {"foreign": {}}}, {"Containers": None}])
def test_mutated_or_nonempty_network_is_not_removed(change):
    owner, info, run = fixture()
    owner.capture(NAME)
    info.update(change)
    with pytest.raises(ValueError, match="not_owned_empty_network"):
        owner.close()
    assert not any(call.args[:2] == ("network", "rm") for call in run.call_args_list)
    assert owner.identifier == IDENTIFIER


def test_same_name_cannot_replace_the_captured_identity():
    owner, info, _ = fixture()
    owner.capture(NAME)
    info["Id"] = "b" * 64
    with pytest.raises(ValueError, match="identity_invalid"):
        owner.capture(NAME)
    assert owner.identifier == IDENTIFIER


def test_already_removed_network_does_not_inspect_or_remove_a_reused_name():
    owner, _, run = fixture()
    owner.capture(NAME)
    run.reset_mock()
    run.side_effect = None
    run.return_value = ""
    owner.close()
    assert run.call_count == 1 and owner.identifier is None


@pytest.mark.parametrize("present", ["b" * 64, IDENTIFIER + "\n" + "b" * 64])
def test_ambiguous_lookup_is_not_a_cleanup_target(present):
    owner, _, run = fixture()
    owner.capture(NAME)
    run.side_effect = None
    run.return_value = present
    with pytest.raises(ValueError, match="identity_invalid"):
        owner.close()
    assert run.call_count == 2


def test_removal_error_retains_capability_and_remains_failure():
    owner, _, run = fixture()
    owner.capture(NAME)
    previous = run.side_effect

    def failing(*args):
        if args[:2] == ("network", "rm"):
            raise RuntimeError("synthetic removal failure")
        return previous(*args)

    run.side_effect = failing
    with pytest.raises(RuntimeError, match="removal failure"):
        owner.close()
    assert owner.identifier == IDENTIFIER
    run.side_effect = previous
    owner.close()
    assert owner.identifier is None


@pytest.mark.parametrize("failed", [None, "worker", "peer", "bridge", "network"])
def test_each_dependent_closes_before_network_even_when_a_close_fails(failed):
    calls = []

    def close(name):
        calls.append(name)
        if name == failed:
            raise RuntimeError("synthetic cleanup failure")

    worker, peer, network = [Mock(close=lambda name=name: close(name)) for name in ("worker", "peer", "network")]

    def cleanup():
        close_dialog_browsers(worker, peer, "bridge", network, close_bridge=close)

    if failed is None:
        cleanup()
    else:
        with pytest.raises(RuntimeError, match="cleanup failure"):
            cleanup()
    assert calls == ["worker", "peer", "bridge", "network"]


def test_partial_setup_owns_no_worker_browser():
    peer, network, close = Mock(), Mock(), Mock()
    close_dialog_browsers(None, peer, "bridge", network, close_bridge=close)
    peer.close.assert_called_once()
    close.assert_called_once_with("bridge")
    network.close.assert_called_once()
