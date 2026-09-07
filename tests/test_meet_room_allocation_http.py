"""Real Flask adapter and SQL binding fixture, with deterministic headless auth."""

import pytest

from tests import test_meet_integration as binding_fixtures
from tests.test_meet_room_allocation import allocation

runtime = binding_fixtures.runtime
meet_client = binding_fixtures.meet_client
pytestmark = pytest.mark.timeout(45)

PATH = "/api/meet/v1/projects/project-a/binding/allocate"
HEADERS = {"Authorization": "Bearer test-user"}


def test_endpoint_preserves_auth_role_opt_in_and_no_store(meet_client, runtime):
    client, app = meet_client
    assert client.post(PATH, json={"expected_revision": 0}).status_code == 401
    assert client.post(PATH, headers=HEADERS, json={"expected_revision": 0}).status_code == 404
    app.extensions["meet_room_allocation"] = allocation(runtime)[0]
    first = client.post(PATH, headers=HEADERS, json={"expected_revision": 0})
    assert first.status_code == 200 and first.json["revision"] == 1
    assert first.json["membership_granted"] is False and first.json["room_verified"] is False
    assert first.headers["Cache-Control"] == "no-store" and first.headers["Referrer-Policy"] == "no-referrer"
    assert client.post(PATH, headers=HEADERS, json={"expected_revision": 0}).status_code == 409
    assert client.post(PATH, headers=HEADERS, json={"expected_revision": 1}).json == first.json
    task = client.post(
        PATH.replace("/binding", "/tasks/task-a/binding"), headers=HEADERS, json={"expected_revision": 0}
    )
    assert task.status_code == 200 and task.json["task_id"] == "task-a"
    app.config["ROLE"] = "worker"
    assert client.post(PATH, headers=HEADERS, json={"expected_revision": 1}).status_code == 403


@pytest.mark.parametrize(
    "raw",
    [
        b'{"expected_revision":0,"expected_revision":0}',
        b'{"expected_revision":NaN}',
        b'{"expected_revision":0,"url":"https://foreign.test"}',
        b"[]",
        b"null",
        b"\xff",
        b"{",
        b" " * 257,
    ],
)
def test_bad_http_body_has_no_allocation_side_effect(meet_client, runtime, raw):
    client, app = meet_client
    service, factory = allocation(runtime)
    app.extensions["meet_room_allocation"] = service
    response = client.post(PATH, headers=HEADERS, data=raw, content_type="application/json")
    assert response.status_code == 400
    assert response.json == {"error": {"code": "meet_room_allocation_payload_invalid"}}
    factory.assert_not_called()


def test_query_content_type_and_transfer_encoding_are_rejected(meet_client, runtime):
    client, app = meet_client
    service, factory = allocation(runtime)
    app.extensions["meet_room_allocation"] = service
    assert client.post(PATH + "?approve=true", headers=HEADERS, json={"expected_revision": 0}).status_code == 400
    assert client.post(PATH, headers=HEADERS, data='{"expected_revision":0}').status_code == 400
    assert (
        client.post(PATH, headers=HEADERS | {"Transfer-Encoding": "chunked"}, json={"expected_revision": 0}).status_code
        == 400
    )
    factory.assert_not_called()
