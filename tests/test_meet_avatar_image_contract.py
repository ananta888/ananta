"""Synthetic image hydration bindings and strict, separate response envelope."""

import copy
import json

import pytest

from ananta_contracts.meet_avatar_image import (
    MAX_AVATAR_IMAGE_BYTES,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    decode_image_response,
    image_request_signature,
    image_response_signature,
    parse_image_message,
    validate_image_request,
)
from ananta_contracts.meet_dialog import MAX_DIALOG_BYTES, request_signature, response_signature
from tests.test_meet_avatar_image_bridge import image_assignment

NOW = 1788000000


def packet():
    binding = {
        "tenant_id": "synthetic",
        "project_id": "test",
        "task_id": "parent",
        "lease_id": "dispatch",
        "runtime_id": "runtime",
        "session_id": "hub-session",
        "own_peer_id": "machine",
        "meet_session_id": "ms_" + "a" * 32,
        "room_id": "room-" + "a" * 18,
        "generation": 1,
        "membership_epoch": 2,
        "avatar_revision": 3,
        "deadline_ms": (NOW + 60) * 1000,
        "selection_digest": "b" * 64,
    }
    request = {"schema": REQUEST_SCHEMA, "nonce": "a" * 32, "sent_at": NOW, "binding": binding}
    response = {
        "schema": RESPONSE_SCHEMA,
        "nonce": request["nonce"],
        "binding": copy.deepcopy(binding),
        "image": image_assignment(),
    }
    return request, response


def test_closed_response_hydrates_only_exact_reference_binding_and_request():
    request, response = packet()
    assert validate_image_request(request, NOW) is request
    assert decode_image_response(response, request, response["image"]["reference"], NOW * 1000) == response["image"]


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "project_id",
        "task_id",
        "lease_id",
        "runtime_id",
        "session_id",
        "own_peer_id",
        "meet_session_id",
        "room_id",
        "generation",
        "membership_epoch",
        "avatar_revision",
        "deadline_ms",
        "selection_digest",
    ],
)
def test_response_cannot_rebind_any_authority_dimension(field):
    request, response = packet()
    old = response["binding"][field]
    response["binding"][field] = old + 1 if type(old) is int else "other"
    with pytest.raises(ValueError):
        decode_image_response(response, request, response["image"]["reference"], NOW * 1000)


@pytest.mark.parametrize("change", ["nonce", "schema", "extra", "expired", "reference", "bytes"])
def test_invalid_or_stale_response_never_releases_image(change):
    request, response = packet()
    reference = copy.deepcopy(response["image"]["reference"])
    now = NOW * 1000
    if change == "nonce":
        response["nonce"] = "c" * 32
    elif change == "schema":
        response["schema"] = REQUEST_SCHEMA
    elif change == "extra":
        response["url"] = "https://image"
    elif change == "expired":
        now = response["binding"]["deadline_ms"]
    elif change == "reference":
        response["image"]["reference"]["artifact_id"] = "other"
    else:
        response["image"]["png"] = "invalid"
    with pytest.raises(ValueError):
        decode_image_response(response, request, reference, now)


@pytest.mark.parametrize(
    "raw", [b"", b'{"x":1,"x":2}', b'{"x":{"a":1,"a":2}}', b'{"x":NaN}', b'{"x":Infinity}', b'"\xff"', b"[" * 2000]
)
def test_strict_parser_rejects_ambiguous_invalid_or_recursive_json(raw):
    with pytest.raises(ValueError):
        parse_image_message(raw)


def test_image_envelope_does_not_expand_ordinary_dialog_request_size():
    raw = json.dumps({"padding": "a" * MAX_DIALOG_BYTES}).encode()
    with pytest.raises(ValueError):
        parse_image_message(raw)
    assert parse_image_message(raw, response=True)["padding"]
    with pytest.raises(ValueError):
        parse_image_message(b" " * (MAX_AVATAR_IMAGE_BYTES + 1), response=True)


def test_new_signatures_bind_direction_protocol_and_exact_request():
    key, request, response = b"synthetic-test-key", b"request", b"response"
    assert image_request_signature(key, request) != request_signature(key, request)
    signature = image_response_signature(key, request, response)
    assert signature != response_signature(key, request, response)
    assert signature != image_request_signature(key, response)
    assert signature != image_response_signature(key, request + b" ", response)
