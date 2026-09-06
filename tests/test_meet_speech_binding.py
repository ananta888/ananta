"""Hub-pinned voice receipts verified against real synthetic WAV bytes."""

import base64
import io
import wave
from copy import deepcopy

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_media_result import validate_response_budget, validate_result
from agent.services.meet_speech_result import validate_speech_binding
from agent.services.meet_turn_service import HubMediaTasks
from ananta_contracts.meet_speech import speech_profile
from tests.test_meet_media import PRINCIPAL, result, service, turn
from worker.meet_media.contract import validate_turn


def speech_result(*, profile=None, rate=22050, samples=441):
    profile = profile if profile is not None else speech_profile(max_seconds=5)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setframerate(rate)
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.writeframes(b"\0\0" * samples)
    return result() | {
        "audio": {"mime": "audio/wav", "base64": base64.b64encode(output.getvalue()).decode()},
        "duration_seconds": round(samples / rate, 3),
        "speech": {"profile": profile, "samples": samples},
    }


def test_optional_profile_preserves_old_turns_and_requires_exact_new_receipts():
    old = turn()
    new = old | {"speech_profile": speech_profile(max_seconds=5)}
    assert validate_turn(new, old["deadline"] - 100) == new
    validate_response_budget(old, result())
    validate_response_budget(new, speech_result())
    assert validate_result(speech_result())
    for request, response in (
        (old, speech_result()),
        (new, result()),
        (new, speech_result(profile=speech_profile(max_seconds=4))),
    ):
        with pytest.raises(MeetError, match="speech_mismatch"):
            validate_response_budget(request, response)


@pytest.mark.parametrize(
    "change", ["samples", "bool_samples", "extra", "duration", "nan", "truncated", "rate", "overbudget"]
)
def test_claimed_voice_success_cannot_hide_wrong_pcm_or_duration(change):
    response = speech_result()
    if change == "samples":
        response["speech"]["samples"] += 1
    elif change == "bool_samples":
        response["speech"]["samples"] = True
    elif change == "extra":
        response["speech"]["untrusted"] = True
    elif change == "duration":
        response["duration_seconds"] = 1
    elif change == "nan":
        response["duration_seconds"] = float("nan")
    elif change == "truncated":
        raw = base64.b64decode(response["audio"]["base64"])
        response["audio"]["base64"] = base64.b64encode(raw[:-2]).decode()
    elif change == "rate":
        response = speech_result(rate=16000)
    else:
        response = speech_result(profile=speech_profile(max_seconds=1), samples=22051)
    with pytest.raises(MeetError):
        validate_speech_binding({"speech_profile": speech_profile(max_seconds=5)}, response)


def test_hub_supplies_the_profile_and_rejects_missing_or_mutated_worker_receipts():
    runtime, _, worker, tasks = service()
    runtime.speech_profile = speech_profile(max_seconds=5)
    seen = []

    def execute(request):
        seen.append(deepcopy(request))
        return speech_result() | {"task_id": request["task_id"], "lease_id": request["lease_id"]}

    worker.execute.side_effect = execute
    runtime.execute(PRINCIPAL, "project", {"text": "Hallo"})
    assert seen[0]["speech_profile"] == speech_profile(max_seconds=5)
    tasks.finish.assert_called_once()
    for payload in ({"text": "Hallo", "speech_profile": speech_profile()}, {"text": "Hallo", "voice_id": "other"}):
        with pytest.raises(MeetError, match="payload_invalid"):
            runtime.execute(PRINCIPAL, "project", payload)
    worker.execute.side_effect = lambda request: result() | {
        "task_id": request["task_id"],
        "lease_id": request["lease_id"],
    }
    with pytest.raises(MeetError, match="speech_mismatch"):
        runtime.execute(PRINCIPAL, "project", {"text": "Hallo"})
    assert tasks.finish.call_args.args[1] == "failed"


def test_real_hub_task_terminal_cas_binds_speech_profile(app):
    import uuid

    request = turn() | {"task_id": str(uuid.uuid4()), "speech_profile": speech_profile(max_seconds=5)}
    tasks = HubMediaTasks()
    with app.app_context():
        tasks.start(request, "actor")
        assert not tasks.finish(request | {"speech_profile": speech_profile(max_seconds=4)}, "completed")
        assert tasks.finish(request, "completed")
