"""Bounded full-room visual receipts, with unchanged request/legacy limits."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from agent.services.meet_authorization_client import validate_authorization
from ananta_contracts.meet_dialog import MAX_VISUAL_CONTROL_BYTES, parse, parse_visual_control
from tests.test_meet_dialog_authority import fixture
from tests.test_meet_receive_capability import receipt


def test_full_nineteen_publisher_four_source_receipt_fits_only_negotiated_response_limit():
    f = fixture()
    scope = replace(f.authority.current("task", "dispatch", "runtime"), capabilities=("audio.receive", "video.receive"))
    value, args = receipt(scope, f.now, [])
    value["grants"], value["publications"] = [], []
    for publisher in range(1, 20):
        peer = f"{publisher:016x}"
        publications = []
        for index, source in enumerate(["microphone", "screen-audio", "camera", "screen"]):
            publication = f"{peer}-{index}-".ljust(128, "x")
            publications.append(publication)
            value["publications"].append(
                {"peerId": peer, "publicationId": publication, "source": source, "publicationEpoch": index + 1}
            )
        value["grants"].append(
            {
                "publisherPeerId": peer,
                "machinePeerId": value["peerId"],
                "publicationIds": publications,
                "chatRead": False,
                "expiresAt": (f.now + 120) * 1000,
            }
        )
    raw = json.dumps(value).encode()
    assert 16384 < len(raw) < MAX_VISUAL_CONTROL_BYTES
    with pytest.raises(ValueError):
        parse(raw)
    assert validate_authorization(parse_visual_control(raw), *args) == value
    changed = deepcopy(value)
    changed["publications"].append(dict(changed["publications"][0]))
    with pytest.raises(ValueError):
        # This parser still enforces the larger hard byte ceiling.
        parse_visual_control(b" " * MAX_VISUAL_CONTROL_BYTES + b"{}")
    from agent.services.meet_contract import MeetError

    with pytest.raises(MeetError):
        validate_authorization(changed, *args)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff", b""])
def test_negotiated_response_keeps_duplicate_number_and_utf8_rejections(raw):
    with pytest.raises(ValueError):
        parse_visual_control(raw)
