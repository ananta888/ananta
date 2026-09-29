from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from agent.services.vector_index_worker_identity_service import (
    VectorIndexWorkerIdentityError,
    authenticate_vector_index_worker,
)
from worker.retrieval.vector_index_dispatch_admission import (
    HubVectorIndexDispatchAdmissionClient,
)

_TOKEN = "worker-service-token-" + ("a" * 40)
_CAPABILITIES = [
    "retrieval",
    "index_write",
    "vector_index_operation",
]


def _registration_config(tmp_path) -> dict[str, str]:
    path = tmp_path / "worker-registration-keyring.json"
    path.write_text(
        json.dumps(
            {
                "schema": (
                    "ananta.workflow-worker-registration-keyring.v1"
                ),
                "workers": {
                    "worker-a": {
                        "worker_url": "http://worker-a:5000",
                        "registration_token": (
                            "worker-registration-token-"
                            + ("b" * 40)
                        ),
                        "service_token_sha256": hashlib.sha256(
                            _TOKEN.encode("utf-8")
                        ).hexdigest(),
                        "session_signing_key_sha256": (
                            hashlib.sha256(
                                (
                                    "worker-session-key-"
                                    + ("c" * 40)
                                ).encode("utf-8")
                            ).hexdigest()
                        ),
                        "allowed_capabilities": _CAPABILITIES,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return {
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": (
            str(path)
        )
    }


def _worker(**overrides):
    values = {
        "name": "worker-a",
        "url": "http://worker-a:5000",
        "token": _TOKEN,
        "role": "worker",
        "status": "online",
        "registration_validated": True,
        "registration_provenance": (
            "strict_registration_keyring_v1"
        ),
        "authorized_capabilities": _CAPABILITIES,
        "capabilities": _CAPABILITIES,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_worker_identity_is_exact_and_least_privilege(
    tmp_path,
) -> None:
    config = _registration_config(tmp_path)
    identity = authenticate_vector_index_worker(
        provided_token=_TOKEN,
        claimed_worker_id="worker-a",
        claimed_worker_url="HTTP://WORKER-A:5000/",
        registered_agents=[_worker()],
        forbidden_tokens=["hub-" + ("x" * 40)],
        config=config,
    )

    assert identity.worker_id == "worker-a"
    assert identity.worker_url == "http://worker-a:5000"

    for values, reason in (
        (
            {"claimed_worker_url": "http://worker-b:5000"},
            "vector_index_worker_identity_forbidden",
        ),
        (
            {
                "registered_agents": [
                    _worker(
                        authorized_capabilities=[
                            "retrieval",
                            "index_write",
                            "vector_index_operation",
                        ],
                        capabilities=[
                            "retrieval",
                            "index_write",
                        ],
                    )
                ]
            },
            "vector_index_worker_identity_forbidden",
        ),
        (
            {"forbidden_tokens": [_TOKEN]},
            "vector_index_worker_credential_reuse_denied",
        ),
    ):
        arguments = {
            "provided_token": _TOKEN,
            "claimed_worker_id": "worker-a",
            "claimed_worker_url": "http://worker-a:5000",
            "registered_agents": [_worker()],
            "forbidden_tokens": (),
            "config": config,
            **values,
        }
        with pytest.raises(
            VectorIndexWorkerIdentityError,
            match=reason,
        ):
            authenticate_vector_index_worker(**arguments)


class _Response:
    def __init__(
        self,
        payload: object,
        *,
        status_code: int = 200,
        headers: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, **_kwargs):
        yield self._body

    def close(self):
        self.closed = True


def _granted_payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "allowed": True,
        "reason_code": "vector_index_dispatch_admission_granted",
        "job_id": "vector-index-job",
        "attempt_id": "dispatch-attempt-00000001",
        "sequence": 3,
        "phase": "execute",
        "worker_audience": "http://worker-a:5000",
    }
    data.update(overrides)
    return {"status": "success", "data": data}


def test_worker_admission_client_is_bounded_and_redirect_safe() -> None:
    calls: list[dict] = []
    success = _Response(_granted_payload())

    def post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return success

    client = HubVectorIndexDispatchAdmissionClient(
        hub_url="http://hub:5000",
        worker_id="worker-a",
        worker_url="http://worker-a:5000",
        token_provider=lambda: _TOKEN,
        post=post,
    )
    client.admit(
        job_id="vector-index-job",
        attempt_id="dispatch-attempt-00000001",
        sequence=3,
        phase="execute",
        worker_audience="http://worker-a:5000",
    )

    assert calls[0]["allow_redirects"] is False
    assert calls[0]["stream"] is True
    assert (
        calls[0]["headers"]["Authorization"]
        == f"Bearer {_TOKEN}"
    )
    assert success.closed is True

    redirect = _Response({}, status_code=307)
    client._post = lambda *_args, **_kwargs: redirect
    with pytest.raises(
        RuntimeError,
        match="vector_index_dispatch_admission_redirect",
    ):
        client.admit(
            job_id="vector-index-job",
            attempt_id="dispatch-attempt-00000002",
            sequence=4,
            phase="execute",
            worker_audience="http://worker-a:5000",
        )
    assert redirect.closed is True

    oversized = _Response(
        {},
        headers={"Content-Length": "20000"},
    )
    client._post = lambda *_args, **_kwargs: oversized
    with pytest.raises(
        RuntimeError,
        match=(
            "vector_index_dispatch_admission_response_too_large"
        ),
    ):
        client.admit(
            job_id="vector-index-job",
            attempt_id="dispatch-attempt-00000003",
            sequence=5,
            phase="execute",
            worker_audience="http://worker-a:5000",
        )
    assert oversized.closed is True


@pytest.mark.parametrize(
    ("payload", "status_code"),
    [
        (_granted_payload(), 201),
        (
            {
                **_granted_payload(),
                "message": "unexpected",
            },
            200,
        ),
        (
            {
                "status": "error",
                "data": _granted_payload()["data"],
            },
            200,
        ),
        (
            _granted_payload(unexpected=True),
            200,
        ),
        (
            _granted_payload(job_id="vector-index-other"),
            200,
        ),
        (
            _granted_payload(
                attempt_id="dispatch-attempt-00000002"
            ),
            200,
        ),
        (
            _granted_payload(sequence=4),
            200,
        ),
        (
            _granted_payload(phase="propose"),
            200,
        ),
        (
            _granted_payload(
                worker_audience="http://worker-b:5000"
            ),
            200,
        ),
        (
            _granted_payload(reason_code="unexpected"),
            200,
        ),
        (
            _granted_payload(allowed=False),
            200,
        ),
    ],
)
def test_worker_admission_client_rejects_unbound_success(
    payload: dict[str, object],
    status_code: int,
) -> None:
    response = _Response(payload, status_code=status_code)
    client = HubVectorIndexDispatchAdmissionClient(
        hub_url="http://hub:5000",
        worker_id="worker-a",
        worker_url="http://worker-a:5000",
        token_provider=lambda: _TOKEN,
        post=lambda *_args, **_kwargs: response,
    )

    with pytest.raises(
        RuntimeError,
        match="vector_index_dispatch_admission_response_invalid",
    ):
        client.admit(
            job_id="vector-index-job",
            attempt_id="dispatch-attempt-00000001",
            sequence=3,
            phase="execute",
            worker_audience="http://worker-a:5000",
        )
    assert response.closed is True


def test_internal_admission_route_binds_authenticated_worker(
    client,
    app,
    monkeypatch,
    tmp_path,
) -> None:
    from agent.db_models import AgentInfoDB
    from agent.services.repository_registry import (
        get_repository_registry,
    )
    from agent.services import vector_index_task_service

    calls: list[dict] = []
    worker = AgentInfoDB(
        url="http://worker-a:5000",
        name="worker-a",
        role="worker",
        token=_TOKEN,
        capabilities=[
            "retrieval",
            "index_write",
            "vector_index_operation",
        ],
        authorized_capabilities=[
            "retrieval",
            "index_write",
            "vector_index_operation",
        ],
        registration_validated=True,
        registration_provenance=(
            "strict_registration_keyring_v1"
        ),
        status="online",
    )
    with app.app_context():
        app.config.update(_registration_config(tmp_path))
        get_repository_registry().agent_repo.save(worker)
    monkeypatch.setattr(
        vector_index_task_service,
        "get_vector_index_task_service",
        lambda: SimpleNamespace(
            admit_dispatch_attempt=lambda **kwargs: (
                calls.append(kwargs)
                or {
                    "attempt_id": kwargs["attempt_id"],
                    "sequence": kwargs["sequence"],
                    "phase": kwargs["phase"],
                    "audience": kwargs["worker_audience"],
                }
            )
        ),
    )

    response = client.post(
        (
            "/internal/tasks/vector-index-job/"
            "vector-index-dispatch-admission"
        ),
        json={
            "attempt_id": "dispatch-attempt-00000001",
            "sequence": 7,
            "phase": "execute",
        },
        headers={
            "Authorization": f"Bearer {_TOKEN}",
            "X-Ananta-Worker-ID": "worker-a",
            "X-Ananta-Worker-URL": "http://worker-a:5000",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success",
        "data": {
            "allowed": True,
            "reason_code": (
                "vector_index_dispatch_admission_granted"
            ),
            "job_id": "vector-index-job",
            "attempt_id": "dispatch-attempt-00000001",
            "sequence": 7,
            "phase": "execute",
            "worker_audience": "http://worker-a:5000",
        },
    }
    assert calls[0]["worker_audience"] == (
        "http://worker-a:5000"
    )
