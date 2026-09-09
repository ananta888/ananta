"""Closed generation creation surface and separate signed machine protocol."""

from unittest.mock import Mock

import pytest

from ananta_contracts.persona_generation import GenerationWire
from tests import test_persona_generated_source as source_cases
from tests import test_persona_media_routes as route_cases
from tests.test_persona_video_http import post, serving
from worker.meet_media.persona_inspection_server import create_inspection_server

generated = source_cases.generated
client = route_cases.client
pytestmark = pytest.mark.timeout(25)
PATH = "/api/persona-media/v1/projects/project/generated-assets"


def request_body(generated):
    return {
        "recipe": {"profile": "procedural-avatar-v1", "media_kind": "image", "palette": "amber"},
        "license": generated.output.license.model_dump(mode="json"),
        "classification": "test_only",
    }


def test_creation_passes_authenticated_scope_and_defaults_without_implicit_publish(client, generated):
    http, app = client
    creator = app.extensions["persona_generated_assets"] = Mock()
    creator.create.return_value.model_dump.return_value = {"classification": "test_only"}
    response = http.post(PATH, headers=route_cases.HEADERS, json={"request": request_body(generated)})
    assert response.status_code == 201 and response.json["state"] == "active"
    principal, project, request = creator.create.call_args.args
    assert (principal.subject_id, principal.tenant_id, project) == ("actor", "tenant", "project")
    assert request.publish is False and request.valid_for_seconds == 900
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "change",
    [
        {"extra": "shell"},
        {"publish": "true"},
        {"publish": 1},
        {"classification": "production"},
        {"valid_for_seconds": 0},
        {"valid_for_seconds": 3601},
        {"valid_for_seconds": True},
        {"recipe": {"profile": "cloud", "media_kind": "image", "palette": "amber"}},
        {"license": {"source_id": "unregistered", "binding_digest": "a" * 64}},
    ],
)
def test_creation_rejects_open_policy_or_recipe_fields(client, generated, change):
    http, app = client
    creator = app.extensions["persona_generated_assets"] = Mock()
    response = http.post(PATH, headers=route_cases.HEADERS, json={"request": request_body(generated) | change})
    assert response.status_code == 409
    creator.create.assert_not_called()


@pytest.mark.parametrize("mode", ["disabled", "worker", "query"])
def test_creation_is_hub_only_explicitly_enabled_without_query_overrides(client, generated, mode):
    http, app = client
    path = PATH
    if mode == "worker":
        app.config["ROLE"] = "worker"
    if mode == "query":
        path += "?publish=true"
    response = http.post(path, headers=route_cases.HEADERS, json={"request": request_body(generated)})
    assert response.status_code == {"disabled": 409, "worker": 403, "query": 400}[mode]


@pytest.mark.parametrize("attack", ["domain", "path", "duplicate", "size", "length"])
def test_generator_signed_server_rejects_cross_domain_and_ambiguous_requests(attack):
    executor = Mock()
    raw, path, domain, headers, length = b"{}", GenerationWire.path, GenerationWire.domain, (), None
    if attack == "domain":
        domain = b"persona-image-v1"
    elif attack == "path":
        path = "/v1/persona-images"
    elif attack == "duplicate":
        raw = b'{"recipe":{},"recipe":{}}'
    elif attack == "size":
        length = GenerationWire.request_limit + 1
    else:
        headers = (("Content-Length", "2"),)
    from tests.test_persona_video_http import KEY

    with serving(create_inspection_server(("127.0.0.1", 0), KEY, executor, wire=GenerationWire())) as server:
        status, _, _ = post(server.server_port, path, raw, domain=domain, extra_headers=headers, declared_length=length)
    assert status == 409
    executor.execute.assert_not_called()
