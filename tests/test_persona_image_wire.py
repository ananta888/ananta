"""Closed image transport and durable replay tests with Hub-issued test runs."""

import base64
import copy
import io
import time
from unittest.mock import Mock

import pytest

from ananta_contracts.persona_image import validate_assignment
from tests.test_persona_inspection_tasks import execute
from tests.test_persona_inspection_tasks import runtime as runtime
from worker.meet_media.persona_executor import PersonaImageExecutor
from worker.meet_media.persona_http import read_bounded, request_signature, result_signature

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def request_payload(request):
    inspection = request.getfixturevalue("runtime")
    execute(inspection)
    assignment = copy.deepcopy(inspection.worker.execute.call_args.args[0])
    return {
        "assignment": assignment,
        "content": base64.b64encode(inspection.content).decode(),
        "media_type": "image/png",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("deadline", float("nan")),
        ("deadline", float("inf")),
        ("deadline", True),
        ("deadline", 0),
        ("deadline", 2**53),
        ("tenant_id", []),
        ("run_id", "unknown-unregistered"),
        ("admission_digest", "bad"),
        ("source_sha256", {}),
        ("extra", True),
    ],
)
def test_invalid_assignment_is_rejected_before_worker_execution(request_payload, field, value):
    assignment = request_payload["assignment"] | {field: value}
    with pytest.raises(ValueError):
        validate_assignment(assignment, time.time())


def test_wire_projection_cannot_replace_the_exact_reserved_lease(request_payload):
    assignment = request_payload["assignment"]
    assignment["evidence"]["dispatch_lease_id"] = "different"
    with pytest.raises(ValueError):
        validate_assignment(assignment, time.time())


def test_image_replay_fence_survives_worker_reconstruction(request_payload, tmp_path):
    # Explicit synthetic authority port only for this isolated worker boundary.
    guard = Mock()
    ledger = tmp_path / "leases.sqlite"
    first = PersonaImageExecutor(ledger, guard_factory=lambda _: guard)
    result = first.execute(request_payload)
    assert result["task_id"] == request_payload["assignment"]["task_id"]
    second = PersonaImageExecutor(ledger, guard_factory=lambda _: guard)
    with pytest.raises(ValueError, match="replayed"):
        second.execute(request_payload)


def test_single_flight_and_wrong_source_do_not_start_a_decoder(request_payload, tmp_path):
    guards = Mock()
    executor = PersonaImageExecutor(tmp_path / "leases.sqlite", guard_factory=guards)
    executor.lock.acquire()
    try:
        with pytest.raises(ValueError, match="busy"):
            executor.execute(request_payload)
    finally:
        executor.lock.release()
    with pytest.raises(ValueError, match="source_mismatch"):
        executor.execute(request_payload | {"content": base64.b64encode(b"different").decode()})
    guards.assert_not_called()


def test_authority_revocation_prevents_worker_output(request_payload, tmp_path):
    guard = Mock()
    guard.require.side_effect = PermissionError("synthetic revoked authority")
    executor = PersonaImageExecutor(tmp_path / "leases.sqlite", guard_factory=lambda _: guard)
    with pytest.raises(PermissionError, match="revoked"):
        executor.execute(request_payload)


def test_signed_domains_and_each_callback_request_are_separate():
    key = b"synthetic-key-00000000000000000000"
    assert request_signature(key, b"persona-image-v1", b"same") != request_signature(key, b"persona-lease-v1", b"same")
    assert result_signature(key, b"persona-lease-v1", b"nonce-1", b"allowed") != result_signature(
        key, b"persona-lease-v1", b"nonce-2", b"allowed"
    )


def test_bounded_http_reader_rejects_oversize_truncation_and_expiry():
    with pytest.raises(ValueError, match="too_large"):
        read_bounded(io.BytesIO(b"12345"), maximum=4, deadline=time.monotonic() + 1)
    with pytest.raises(ValueError, match="incomplete"):
        read_bounded(io.BytesIO(b"1"), maximum=4, length=3, deadline=time.monotonic() + 1)
    with pytest.raises(ValueError, match="deadline"):
        read_bounded(io.BytesIO(b"1"), maximum=4, deadline=time.monotonic() - 1)


@pytest.mark.parametrize("field", ["repository_revision", "execution_profile_digest", "environment_digest"])
def test_task_adapter_requires_preconfigured_execution_bindings(field):
    from agent.services.persona_inspection_tasks import HubPersonaInspectionTasks

    configuration = dict(repository_revision="a" * 40, execution_profile_digest="b" * 64, environment_digest="c" * 64)
    configuration[field] = ""
    with pytest.raises(ValueError, match="required"):
        HubPersonaInspectionTasks(policy=Mock(), worker=Mock(), state=Mock(), registry=Mock(), **configuration)
