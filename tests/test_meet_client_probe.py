"""Feasibility is local, closed and independent of authority or capture."""

import copy
import itertools
import subprocess

import pytest

from ananta_contracts.meet_client_probe import CODECS, PORTS, require_client_probe
from ananta_contracts.meet_source_profile import CAPABILITIES
from tests.test_meet_dialog_session_operations import Page, setup, starts
from tests.test_meet_publication_session import Page as PublicationPage
from tests.test_meet_publication_session import session as publication_session
from worker.meet_media.client_probe import READ_PROBE, check_client_probe


def projection():
    return {
        "schema": "ananta.meet-client-probe.v1",
        "client": "isolated-browser-v1",
        "frameEnvelope": "codec-prefix-v1",
        "nativeAdapter": False,
        "secureContext": True,
        "encodedTransform": True,
        "codecs": dict.fromkeys(CODECS, True),
        "ports": dict.fromkeys(PORTS, True),
    }


@pytest.mark.parametrize("capability", sorted(CAPABILITIES))
def test_all_assigned_capabilities_have_fixed_browser_feasibility(capability):
    value = projection()
    require_client_probe(value, [capability])
    assert value == projection()


def test_every_nonempty_closed_capability_subset_is_supported_by_the_full_browser_profile():
    capabilities = sorted(CAPABILITIES)
    checked = 0
    for count in range(1, len(capabilities) + 1):
        for subset in itertools.combinations(capabilities, count):
            require_client_probe(projection(), subset)
            checked += 1
    assert checked == 63


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "ananta.meet-client-probe.v2"),
        ("client", "native"),
        ("frameEnvelope", "plaintext"),
        ("nativeAdapter", True),
        ("nativeAdapter", 0),
        ("secureContext", 1),
        ("encodedTransform", "true"),
        ("secret", "private"),
        ("ports", {}),
        ("codecs", []),
    ],
)
def test_unknown_shapes_versions_and_coercion_fail_closed(field, value):
    report = projection()
    report[field] = value
    with pytest.raises(ValueError, match="meet_client_probe_invalid"):
        require_client_probe(report, ["chat.send"])


@pytest.mark.parametrize(
    "kind,field", [("ports", field) for field in sorted(PORTS)] + [("codecs", field) for field in sorted(CODECS)]
)
def test_every_nested_boolean_is_strict(kind, field):
    report = projection()
    report[kind][field] = 1
    with pytest.raises(ValueError, match="meet_client_probe_invalid"):
        require_client_probe(report, [])


@pytest.mark.parametrize("field", ["secureContext", "encodedTransform"])
def test_security_readiness_is_required_even_for_chat(field):
    report = projection()
    report[field] = False
    with pytest.raises(ValueError, match="meet_client_probe_unsupported"):
        require_client_probe(report, ["chat.read"])


@pytest.mark.parametrize(
    "capability,port,codec",
    [
        ("chat.read", "chat", None),
        ("chat.send", "chat", None),
        ("audio.receive", "audio", "opusReceive"),
        ("speech.publish", "speech", "opusSend"),
        ("screen.publish", "screen", "vp8Send"),
        ("avatar.publish", "avatar", "vp8Send"),
    ],
)
def test_only_assigned_ports_and_codec_directions_are_required(capability, port, codec):
    value = projection()
    value["ports"] = dict.fromkeys(PORTS, False)
    value["codecs"] = dict.fromkeys(CODECS, False)
    value["ports"].update(session=True, **{port: True})
    if codec:
        value["codecs"][codec] = True
    require_client_probe(value, [capability])
    for kind, field in [("ports", "session"), ("ports", port)] + ([("codecs", codec)] if codec else []):
        denied = copy.deepcopy(value)
        denied[kind][field] = False
        with pytest.raises(ValueError, match="meet_client_probe_unsupported"):
            require_client_probe(denied, [capability])


def test_mp4_does_not_require_additive_dialog_ports_but_needs_both_sender_codecs():
    value = projection()
    value["ports"] = dict.fromkeys(PORTS, False)
    value["ports"].update(session=True, mp4=True)
    require_client_probe(value, [], mp4=True)
    value["codecs"]["opusSend"] = False
    with pytest.raises(ValueError, match="unsupported"):
        require_client_probe(value, [], mp4=True)


@pytest.mark.parametrize("capabilities", [None, "chat.send", ["capture"], [1], ["chat.send", "chat.send"]])
def test_untrusted_requirements_cannot_select_a_new_profile(capabilities):
    with pytest.raises(ValueError, match="requirements_invalid"):
        require_client_probe(projection(), capabilities)


@pytest.mark.parametrize(
    "result",
    [
        {"present": 0},
        {"present": False, "value": None},
        {"present": True},
        {"present": True, "value": None},
        {},
        None,
        True,
    ],
)
def test_invalid_present_result_never_becomes_legacy(result):
    page = Page()
    page.evaluate = lambda expression: result
    with pytest.raises(ValueError, match="meet_client_probe_invalid"):
        check_client_probe(page, lambda: None, [])


def test_absence_and_success_remain_distinct_and_do_not_hand_off_a_grant():
    page = Page()
    assert check_client_probe(page, lambda: None, []) is False
    page.evaluate = lambda expression: {"present": True, "value": projection()}
    assert check_client_probe(page, lambda: None, ["screen.publish"]) is True
    assert not starts(page)


@pytest.mark.parametrize("mutation", ["invalid", "unsupported", "navigation", "expiry"])
def test_session_probe_failure_closes_before_join_and_rechecks_after_read(mutation):
    page, session = setup()
    original = page.evaluate

    def read(expression, arg=None):
        if expression != READ_PROBE:
            return original(expression, arg)
        value = projection()
        if mutation == "invalid":
            value["unknown"] = True
        elif mutation == "unsupported":
            value["ports"]["screen"] = False
        elif mutation == "navigation":
            page.url += "/foreign"
        else:
            page.now += 21
        return {"present": True, "value": value}

    page.evaluate = read
    with pytest.raises(ValueError):
        session.ready(["screen.publish"])
    assert session.closed and not starts(page)
    with pytest.raises(ValueError):
        session.join("synthetic-room", "private-grant")
    assert not starts(page)


@pytest.mark.parametrize("mutation", ["invalid", "unsupported", "navigation", "expiry"])
def test_mp4_checks_feasibility_before_reading_media_or_handing_off_the_grant(mutation):
    page = PublicationPage()
    original = page.evaluate

    def read(expression, arg=None):
        if expression != READ_PROBE:
            return original(expression, arg)
        value = projection()
        if mutation == "invalid":
            value["client"] = "native"
        elif mutation == "unsupported":
            value["codecs"]["opusSend"] = False
        elif mutation == "navigation":
            page.url += "/foreign"
        else:
            page.now += 21
        return {"present": True, "value": value}

    page.evaluate = read
    with pytest.raises(ValueError):
        publication_session(page).ready()
    assert not page.calls


def test_actual_reader_distinguishes_missing_throwing_promise_and_malformed_ports():
    script = """
const assert = require('node:assert/strict');
const read = eval('(' + process.argv[1] + ')');
global.window = {anantaMachine:{}};
assert.deepEqual(read(), {present:false});
for (const probe of [null, 1, () => {throw Error('private')}, () => new Promise(() => {})]) {
  window.anantaMachine.probe = probe;
  assert.deepEqual(read(), {present:true,value:null});
}
window.anantaMachine.probe = () => ({schema:'synthetic'});
assert.deepEqual(read(), {present:true,value:{schema:'synthetic'}});
"""
    result = subprocess.run(["node", "-e", script, READ_PROBE], capture_output=True, timeout=5)
    assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""
