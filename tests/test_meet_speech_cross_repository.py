"""Actual local Piper/CUDA -> Worker PCM sink -> required-SFrame receiver.

Fixed synthetic input and synthetic Hub admission; not a production dialog run.
"""

import base64
import json
import os
import selectors
import subprocess
import time
from pathlib import Path

import pytest

from tests.meet_gpu_source_fixture import run_probe
from worker.meet_media.audio_output import FRAME_SAMPLES, SpeechFrame
from worker.meet_media.speech_publication import SpeechPublication


class SpeechBridge:
    def __init__(self, process):
        self.process = process

    def receive(self, seconds=15):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(seconds):
                raise ValueError("test_speech_bridge_timeout")
            line = self.process.stdout.readline(4097)
        if not line or len(line) > 4096:
            raise ValueError("test_speech_bridge_missing_or_oversize")
        value = json.loads(line)
        if "error" in value:
            raise ValueError("test_speech_bridge_failed")
        return value

    def call(self, op, **fields):
        self.process.stdin.write(json.dumps({"op": op, **fields}) + "\n")
        self.process.stdin.flush()
        return self.receive()

    def open(self, source_id, total_samples):
        return self.call("open", source_id=source_id, total_samples=total_samples)

    def status(self):
        return self.call("status")

    def push(self, generation, start_sample, pcm_base64):
        if self.call("push", generation=generation, start_sample=start_sample, pcm=pcm_base64) != {"accepted": True}:
            raise ValueError("test_speech_bridge_push_failed")

    def close(self, generation):
        return self.call("close", generation=generation)


@pytest.mark.skipif(
    os.environ.get("MEET_SPEECH_CROSS_GATE") != "1", reason="opt-in real GPU/private Meet browser probe"
)
@pytest.mark.timeout(180)
@pytest.mark.parametrize("receiver", ["chromium", "firefox"])
def test_actual_piper_pcm_reaches_private_meet_decoder(receiver, record_property):
    report = run_probe("speech_pcm_probe")
    pcm = base64.b64decode(report.pop("pcm_base64"), validate=True)
    assert report["engine"] == "piper-cuda" and report["sample_rate"] == 22050
    assert len(pcm) == 2 * report["samples"] <= 441000
    meet = Path(__file__).resolve().parents[2] / "webrtc-minimize-server"
    assert (meet / "dist/browser/assets/machine-speech.worklet.js").is_file(), "Build current companion source first"
    image = subprocess.run(
        ["docker", "inspect", "webrtc-minimize-server-webrtc-1", "--format", "{{.Image}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    process = subprocess.Popen(
        ["node", "test/helpers/machine-speech-bridge.mjs"],
        cwd=meet,
        env=os.environ | {"MEET_TEST_PROXY_IMAGE": image, "MEET_SPEECH_RECEIVER": receiver},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    bridge, output = SpeechBridge(process), None
    deadline = time.monotonic() + 70

    def current():
        if time.monotonic() >= deadline or process.poll() is not None:
            raise ValueError("test_speech_bridge_expired")

    try:
        assert bridge.receive(45) == {"ready": True}
        output = SpeechPublication(bridge, "synthetic-speech-hub-session", report["samples"], current)
        for start in range(0, report["samples"], FRAME_SAMPLES):
            frame = SpeechFrame(start, pcm[2 * start : 2 * (start + FRAME_SAMPLES)])
            while not output.push(frame):
                current()
                time.sleep(0.02)
        while not output.completed:
            output.writable_samples()
            time.sleep(0.02)
        decoded = bridge.call("probe")
        assert decoded["peak"] > 0.02 and decoded["active_windows"] > 10, "actual decrypted non-silent speech required"
        assert decoded["captures"] == decoded["machine_captures"] == decoded["transform_errors"] == 0
        assert output.played == report["samples"]
        record_property(
            "speech_delivery",
            report
            | {
                "receiver": receiver,
                "decoded": decoded,
                "hub_dialog_verified": False,
                "remote_exact_sample_delivery_verified": False,
            },
        )
    finally:
        if output is not None:
            output.close()
        try:
            if process.poll() is None:
                process.stdin.write('{"op":"finish"}\n')
                process.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()
        try:
            process.wait(timeout=100)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            process.stdout.close()
        pcm = b""  # Immutable Python bytes are not claimed to be securely zeroized.
    assert process.returncode == 0
