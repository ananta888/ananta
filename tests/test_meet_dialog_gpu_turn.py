"""Actual current-source GPU HTTP component gate, not a Hub/release identity."""

import base64
import os
import time

import pytest

from agent.services.meet_media_transport import HttpMediaWorker
from ananta_contracts.meet_speech import speech_profile
from ananta_contracts.meet_speech_audio import decode_speech_wav
from tests.meet_dialog_gpu_fixture import DialogGpuFixture
from worker.meet_media.contract import SCHEMA


@pytest.mark.skipif(os.environ.get("MEET_DIALOG_GPU_GATE") != "1", reason="opt-in real private GPU inference gate")
@pytest.mark.timeout(180)
def test_current_private_worker_runs_actual_qwen_piper_and_nvenc(record_property):
    started = time.monotonic()
    with DialogGpuFixture(packaged_image=os.environ.get("MEET_DIALOG_GPU_PACKAGED_IMAGE")) as fixture:
        # Prepare only the pinned model, as in the actual dialog fixture. The
        # request's original 60-second execution budget is never extended.
        record_property("cold_model_preload_seconds", fixture.preload())
        turn = {
            "schema": SCHEMA,
            "task_id": "synthetic-gpu-component",
            "lease_id": "synthetic-gpu-component-lease",
            "tenant_id": "synthetic",
            "project_id": "synthetic",
            "deadline": int(time.time()) + 60,
            "text": "Antworte nur mit: Hallo, ich bin Ananta.",
            "response_limits": {"max_reply_chars": 200, "max_output_tokens": 64},
            "speech_profile": speech_profile(max_seconds=20),
        }
        result = HttpMediaWorker(fixture.endpoint, fixture.key).execute(turn)
        assert result["task_id"] == turn["task_id"] and result["lease_id"] == turn["lease_id"]
        assert result["engines"] == {"llm": "ollama", "speech": "piper-cuda", "video": "procedural-avatar-h264_nvenc"}
        pcm = decode_speech_wav(result["audio"], result["speech"], result["duration_seconds"])
        assert len(pcm) > 882 and any(pcm), "nonempty non-silent actual speech required"
        assert 0 < result["usage"]["output_tokens"] <= 64
        video_bytes = len(base64.b64decode(result["video"]["base64"], validate=True))
        assert video_bytes > 1000
        report = {
            "classification": "synthetic_local_technical_observation",
            "production_release_evidence": False,
            "hub_dialog_verified": False,
            "remote_delivery_verified": False,
            "human_capture_used": False,
            "worker_image": fixture.image,
            "packaged_worker": fixture.packaged_image is not None,
            "model_preloaded": True,
            "speech_samples": len(pcm) // 2,
            "video_bytes": video_bytes,
            "usage": result["usage"],
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
        record_property("gpu_component", report)
