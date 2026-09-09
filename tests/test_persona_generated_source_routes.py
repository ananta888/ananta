"""Headless HTTP/bootstrap adapters; no interactive or implicit grant path."""

import base64
from unittest.mock import Mock

import pytest

from agent.bootstrap.persona_generated_sources import configure_persona_generated_sources
from agent.models.persona_generated_source import PersonaGeneratedOutput, PersonaGenerationRunPin
from tests import test_persona_generated_source as generation_cases
from tests import test_persona_media_routes as http_cases

generated = generation_cases.generated
client = http_cases.client
HEADERS = http_cases.HEADERS

pytestmark = pytest.mark.timeout(30)
PATH = "/api/persona-media/v1/projects/project/generated-sources"


def body(case, pin):
    return {
        "output": case.output.model_dump(mode="json"),
        "run_pin": pin.model_dump(mode="json"),
        "content": base64.b64encode(case.content).decode(),
    }


def test_headless_actual_registry_admission_returns_only_pin_not_publication(client, generated):
    http, app = client
    output = generated.output.model_copy(update={"owner_subject": "actor"})
    pin = generated.reserve(output)
    generated.complete(pin, output)
    app.extensions["persona_generated_sources"] = generated.service
    # The original receipt has a different owner: no cross-owner handoff.
    denied = http.post(PATH, json=body(generated, pin), headers=HEADERS)
    assert denied.status_code == 403
    request_body = body(generated, pin) | {"output": output.model_dump(mode="json")}
    response = http.post(PATH, json=request_body, headers=HEADERS)
    assert response.status_code == 201
    assert set(response.json) == {"source", "publication_authorized"}
    assert response.json["publication_authorized"] is False
    proof = generated.registry.require_source_identity(
        tenant_id="tenant",
        project_id="project",
        source_id=response.json["source"]["source_id"],
        expected_binding_digest=response.json["source"]["binding_digest"],
    )
    assert proof.synthetic and proof.evidence_scope == "test"
    assert response.headers["Cache-Control"] == "no-store"
    app.extensions["persona_assets"].admit_image.assert_not_called()
    app.extensions["persona_image_policy"].install.assert_not_called()


def test_route_passes_closed_models_and_authenticated_scope(client, generated):
    http, app = client
    pin = generated.reserve()
    service = app.extensions["persona_generated_sources"] = Mock()
    service.admit.return_value.model_dump.return_value = {"opaque": "test-port"}
    response = http.post(PATH, json=body(generated, pin), headers=HEADERS)
    assert response.status_code == 201
    args, kwargs = service.admit.call_args
    assert (args[0].subject_id, args[0].tenant_id, args[1]) == ("actor", "tenant", "project")
    assert type(kwargs["output"]) is PersonaGeneratedOutput
    assert type(kwargs["run_pin"]) is PersonaGenerationRunPin
    assert kwargs["content"] == generated.content


@pytest.mark.parametrize("mutation", ["extra", "bytes", "length", "mime", "classification", "run", "duplicate"])
def test_invalid_http_never_calls_source_admission(client, generated, mutation):
    import json

    http, app = client
    service = app.extensions["persona_generated_sources"] = Mock()
    data = body(generated, generated.reserve())
    if mutation == "extra":
        data["publish"] = True
    elif mutation == "bytes":
        data["content"] = []
    elif mutation == "length":
        data["content"] = "AAAA"
    elif mutation == "mime":
        data["output"]["media_type"] = "image/svg+xml"
    elif mutation == "classification":
        data["output"]["classification"] = "production"
    elif mutation == "run":
        data["run_pin"]["run_id"] = "invented"
    raw = json.dumps(data)
    if mutation == "duplicate":
        raw = '{"content":"AAAA",' + raw[1:]
    assert http.post(PATH, data=raw, content_type="application/json", headers=HEADERS).status_code == 409
    service.admit.assert_not_called()


@pytest.mark.parametrize("token", [None, "worker-token", "service-token"])
def test_generated_source_needs_explicit_user_management_auth(client, generated, token):
    http, app = client
    service = app.extensions["persona_generated_sources"] = Mock()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    assert http.post(PATH, json=body(generated, generated.reserve()), headers=headers).status_code == 401
    service.admit.assert_not_called()


def test_disabled_nonhub_and_query_variants_cannot_start_admission(client, generated):
    http, app = client
    payload = body(generated, generated.reserve())
    assert http.post(PATH, json=payload, headers=HEADERS).status_code == 409
    service = app.extensions["persona_generated_sources"] = Mock()
    app.config["ROLE"] = "worker"
    assert http.post(PATH, json=payload, headers=HEADERS).status_code == 403
    app.config["ROLE"] = "hub"
    assert http.post(PATH + "?publish=true", json=payload, headers=HEADERS).status_code == 400
    service.admit.assert_not_called()


@pytest.mark.parametrize("role,enabled", [("worker", "1"), ("hub", "0"), ("hub", "true")])
def test_bootstrap_is_explicit_hub_only(client, monkeypatch, role, enabled):
    _, app = client
    monkeypatch.setenv("ANANTA_PERSONA_GENERATED_SOURCES_ENABLED", enabled)
    app.config["ROLE"] = role
    configure_persona_generated_sources(app)
    assert "persona_generated_sources" not in app.extensions


def test_bootstrap_only_wires_existing_registry_and_project_authority(client, monkeypatch):
    import agent.services.hub_evidence_registry_service as evidence

    _, app = client
    monkeypatch.setenv("ANANTA_PERSONA_GENERATED_SOURCES_ENABLED", "1")
    registry = Mock()
    monkeypatch.setattr(evidence, "get_hub_evidence_registry_service", lambda: registry)
    access = app.extensions["project_access_authority"] = Mock()
    configure_persona_generated_sources(app)
    service = app.extensions["persona_generated_sources"]
    assert service.registry is registry and service.access is access
    assert not registry.mock_calls and not access.mock_calls
