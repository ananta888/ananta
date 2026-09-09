"""Closed synthetic execution profiles and exact Hub/Worker sample boundaries."""

import base64
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.models.meet_preauthorization_binding import assignment_projection
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_audio_profile import AudioReceiveProfile, parse_audio_profile
from ananta_contracts.meet_dialog import validate_assignment, validate_callback
from tests.test_meet_audio_receive import binding, response, wav
from tests.test_meet_dialog_audio import runtime
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_dialog_transport import assignment
from tests.test_meet_dialog_worker_router import fixture as router_fixture
from worker.meet_media.asr_pipeline import MeetAsrPipeline
from worker.meet_media.audio_batch import AudioBatchCursor

PROFILE = AudioReceiveProfile(language="en", vad="off", segment_seconds=2)
pytestmark = pytest.mark.timeout(45)


@pytest.mark.parametrize(
    "patch",
    [
        {"schema": "other"},
        {"language": "auto"},
        {"language": []},
        {"model": "cloud"},
        {"model": "/models/other"},
        {"vad": "auto"},
        {"vad": True},
        {"segment_seconds": 0},
        {"segment_seconds": 11},
        {"segment_seconds": True},
        {"segment_seconds": 1.5},
        {"url": "https://provider.invalid"},
        {"record": True},
        {"prompt": "private"},
    ],
)
def test_profile_rejects_provider_paths_capture_and_unbounded_options(patch):
    value = PROFILE.projection() | patch
    with pytest.raises(ValueError):
        parse_audio_profile(value)
    wire = assignment() | {"audio_mode": "transcribe", "capabilities": ["audio.receive"], "audio_profile": value}
    with pytest.raises(ValueError):
        validate_assignment(wire, time.time())


@pytest.mark.parametrize("seconds", range(1, 11))
@pytest.mark.parametrize("language", ["de", "en"])
def test_profile_roundtrip_has_exact_bounded_sample_ceiling(seconds, language):
    profile = replace(PROFILE, segment_seconds=seconds, language=language)
    assert parse_audio_profile(profile.projection()) == profile
    assert profile.end_sample == seconds * 16000


def test_actual_hub_task_binds_explicit_profile_and_preauthorization_digest(app):
    with app.app_context():
        f = system(profiles=False)
        f.f.authority.policies[("tenant", "project")] = frozenset({"audio.receive"})
        payload = f.payload | {
            "capabilities": ["audio.receive"],
            "audio_mode": "transcribe",
            "audio_profile": PROFILE.projection(),
        }
        started = f.service.start(f.principal, "project", payload)
        wire = f.worker.start_dialog.call_args.args[0]
        assert wire["audio_profile"] == PROFILE.projection()
        assert validate_assignment(wire, f.f.now) == wire
        task = f.tasks.get_by_id(started["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        assert context["audio_profile"] == PROFILE.projection()
        # Original preauthorization projection binds profile changes, not just capabilities.
        context = context | {"binding_task_id": "parent"}
        projection = assignment_projection(task.id, "tenant", "project", f.f.binding.profile.origin, context)
        assert projection["audio_profile"] == PROFILE.projection()
        changed = assignment_projection(
            task.id,
            "tenant",
            "project",
            f.f.binding.profile.origin,
            context | {"audio_profile": replace(PROFILE, language="de").projection()},
        )
        assert changed != projection


@pytest.mark.parametrize("mode", ["off", "transcribe"])
@pytest.mark.parametrize("profile", [None, {}, {"model": "cloud"}])
def test_hub_rejects_invalid_profile_before_worker_dispatch(app, mode, profile):
    with app.app_context():
        f = system(profiles=False)
        f.f.authority.policies[("tenant", "project")] = frozenset({"audio.receive"})
        with pytest.raises(MeetError, match="audio_profile_invalid"):
            f.service.start(
                f.principal,
                "project",
                f.payload
                | {
                    "capabilities": ["audio.receive"],
                    "audio_mode": mode,
                    "audio_profile": profile,
                },
            )
        f.worker.start_dialog.assert_not_called()


@pytest.mark.parametrize("mutation", ["missing", "changed", "injected"])
def test_worker_router_rejects_profile_not_identical_to_current_hub_scope(mutation):
    f = router_fixture()
    f.value.update(audio_mode="transcribe", capabilities=["audio.receive"])
    f.scope.audio_mode, f.scope.capabilities = "transcribe", ["audio.receive"]
    f.scope.audio_profile = PROFILE
    if mutation != "missing":
        f.value["audio_profile"] = replace(PROFILE, language="de").projection()
    if mutation == "injected":
        f.scope.audio_profile = None
    with pytest.raises(MeetError, match="publisher_binding_denied"):
        f.router.start_dialog(f.value)
    f.second.start_dialog.assert_not_called()


@pytest.mark.parametrize("patch", [{}, {"end_sample": 160000}, {"end_sample": True}, {"language": "de"}])
def test_child_result_must_match_original_profile_not_just_global_wire_bounds(patch):
    f, receipt, service, payload = runtime()
    f.context["audio_profile"] = PROFILE.projection()
    job = service.start(payload)["job"]
    assert job["audio_profile"] == PROFILE.projection()
    result = (
        payload
        | {
            "audio_task_id": job["task_id"],
            "audio_lease_id": job["lease_id"],
            "end_sample": 32000,
            "language": "en",
            "text": "ephemeral",
        }
        | patch
    )
    if patch:
        with pytest.raises(MeetError, match="result_invalid"):
            service.complete(result)
    else:
        assert service.complete(result)["reply"] is None
    assert "ephemeral" not in str(f.tasks.finish_audio.call_args)


def test_profile_mutation_revokes_already_admitted_child():
    f, receipt, service, payload = runtime()
    f.context["audio_profile"] = PROFILE.projection()
    job = service.start(payload)["job"]
    f.context["audio_profile"] = replace(PROFILE, vad="local-vad-v1").projection()
    with pytest.raises(MeetError, match="policy_denied"):
        service.current(("task", "dispatch", "runtime"), job)


def batch(start, count, completed=False):
    return {
        "schema": "ananta.meet-audio-batch.draft1",
        "acknowledged": start,
        "completed": completed,
        "chunks": [
            {"sequence": seq + 1, "startSample": seq * 1600, "pcmBase64": base64.b64encode(b"\0" * 3200).decode()}
            for seq in range(start, start + count)
        ],
    }


def test_batch_cursor_preserves_contiguous_samples_without_retaining_pcm():
    cursor = AudioBatchCursor(PROFILE)
    for start in range(0, 20, 5):
        value = batch(start, 5, completed=start >= 15)
        chunks = cursor.validate(value)
        assert cursor.sequence == start
        for chunk in chunks:
            assert chunk.start_sample == (chunk.sequence - 1) * 1600
            assert "pcm=" not in repr(chunk)
            cursor.acknowledge(chunk.sequence)
        assert cursor.complete(value) == (start == 15)
    assert vars(cursor) == {"sequence": 20, "maximum": 20}
    with pytest.raises(ValueError):
        cursor.validate(batch(20, 1, True))


@pytest.mark.parametrize("case", ["oversize", "sequence", "offset", "pcm", "ack", "truncated", "unknown", "completed"])
def test_invalid_batch_does_not_advance_cursor(case):
    cursor = AudioBatchCursor(PROFILE)
    value = batch(0, 1)
    if case == "oversize":
        value = batch(0, 6)
    if case == "sequence":
        value["chunks"][0]["sequence"] = 2
    if case == "offset":
        value["chunks"][0]["startSample"] = True
    if case == "pcm":
        value["chunks"][0]["pcmBase64"] = "YQ=="
    if case == "ack":
        value["acknowledged"] = 1
    if case == "truncated":
        value = batch(0, 0, True)
    if case == "unknown":
        value["extra"] = True
    if case == "completed":
        value["completed"] = 1
    with pytest.raises(ValueError):
        cursor.validate(value)
    assert cursor.sequence == 0


def test_profile_reaches_only_local_asr_child_with_matching_language_and_duration():
    runner = Mock()
    runner.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(response(language="en")).encode())
    pipeline = MeetAsrPipeline(
        binding(), Mock(), deadline_monotonic=time.monotonic() + 29, runner=runner, audio_profile=PROFILE.projection()
    )
    assert pipeline.transcribe(filename="ignored", content=wav(), language="en").language == "en"
    payload = json.loads(runner.run.call_args.kwargs["input_payload"])
    assert set(payload) == {"wav", "language", "audio_profile"}
    assert payload["audio_profile"] == PROFILE.projection()
    with pytest.raises(ValueError, match="profile_mismatch"):
        pipeline.transcribe(filename="ignored", content=wav(), language="de")
    runner.run.return_value.stdout = json.dumps(response(language="en", duration_ms=2001)).encode()
    with pytest.raises(ValueError, match="failed_or_revoked"):
        pipeline.transcribe(filename="ignored", content=wav(), language="en")


@pytest.mark.parametrize("samples", [0, True, 15999, 16001, 160001])
def test_transcript_wire_still_rejects_noncanonical_or_unbounded_segments(samples):
    now = int(time.time())
    value = {
        "schema": "ananta.meet-dialog-callback.v1",
        "action": "transcript",
        "task_id": "task",
        "lease_id": "lease",
        "runtime_id": "runtime",
        "nonce": "a" * 32,
        "sent_at": now,
        "meet_session_id": "ms_" + "a" * 32,
        "audio_task_id": "child",
        "audio_lease_id": "child-lease",
        "end_sample": samples,
        "language": "en",
        "text": "synthetic",
    }
    with pytest.raises(ValueError, match="transcript_invalid"):
        validate_callback(value, now)
