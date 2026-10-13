"""Real bounded media pipes with synthetic clips, no GPU or publishing rights."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from worker.meet_media.companion_media_preparation import validate_clip
from worker.meet_media.companion_media_process import CompanionMediaPreparationProcess
from worker.meet_media.companion_process_transport import CompanionExecutionError

pytestmark = pytest.mark.timeout(10)

HELPER = """
import base64, hashlib, json, sys, time
data = b'\\x00\\x00\\x00\\x10ftypisom0000'
for line in sys.stdin:
    request = json.loads(line)
    time.sleep(0.15)
    clip = {'mp4': base64.b64encode(data).decode(), 'sha256': hashlib.sha256(data).hexdigest(),
            'frames': 2, 'repeatMode': 'loop' if request['kind'] == 'idle' else 'hold_last',
            'originKind': 'generated', 'classification': 'synthetic'}
    result = clip if request['kind'] == 'idle' else [clip]
    print(json.dumps({'kind': request['kind'], 'result': result}), flush=True)
"""


def test_idle_and_large_pcm_preparation_preserve_one_helper_and_keep_owner_active():
    media = CompanionMediaPreparationProcess(command=(sys.executable, "-u", "-c", HELPER))
    ticks = []
    try:
        assert media.idle("thinking", cooperate=lambda: ticks.append(True))["repeatMode"] == "loop"
        process = media._transport.process
        clips = media.speaking(b"\x00\x01" * 970_200, cooperate=lambda: ticks.append(True))
        assert clips[0]["repeatMode"] == "hold_last" and len(ticks) >= 4
        assert media._transport.process is process
    finally:
        media.close()
    assert process.poll() is not None


@pytest.mark.parametrize("kind", ["idle", "speaking"])
def test_media_control_loss_cancels_owned_process_without_restart(kind):
    from worker.meet_media.companion_chat_pump import CompanionSessionEnded

    media = CompanionMediaPreparationProcess(command=(sys.executable, "-u", "-c", "import time; time.sleep(20)"))

    def revoke():
        raise CompanionSessionEnded("synthetic_source_revoked")

    def invoke():
        return (media.idle("idle", cooperate=revoke) if kind == "idle"
                else media.speaking(b"\x00\x01", cooperate=revoke))
    with pytest.raises(CompanionSessionEnded):
        invoke()
    assert media._transport.process.poll() is not None
    with pytest.raises(CompanionExecutionError, match="execution_closed"):
        media.idle("idle", cooperate=lambda: None)


def test_media_timeout_kills_renderer_ignoring_term():
    code = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)"
    media = CompanionMediaPreparationProcess(
        command=(sys.executable, "-u", "-c", code),
        timeout_seconds=0.2)
    with pytest.raises(CompanionExecutionError, match="execution_timeout"):
        media.speaking(b"\x00\x01", cooperate=lambda: None)
    assert media._transport.process.poll() is not None


def test_unknown_or_broadened_media_receipt_is_not_published():
    media = CompanionMediaPreparationProcess(command=(sys.executable, "-u", "-c",
        "print('{\"kind\":\"idle\",\"result\":{},\"policy\":true}',flush=True)"))
    with pytest.raises(CompanionExecutionError, match="media_receipt_invalid"):
        media.idle("idle", cooperate=lambda: None)
    assert media._transport.process.poll() is not None


def test_oversized_media_output_is_stopped_before_receipt():
    media = CompanionMediaPreparationProcess(command=(sys.executable, "-u", "-c",
        "import sys;sys.stdout.write('x'*11000000);sys.stdout.flush()"))
    with pytest.raises(CompanionExecutionError, match="output_limit"):
        media.idle("idle", cooperate=lambda: None)
    assert media._transport.process.poll() is not None


def test_invalid_pcm_and_idle_state_never_launch_media_helper():
    media = CompanionMediaPreparationProcess(command=(sys.executable, "-c", "raise AssertionError('must not run')"))
    for pcm in (b"", b"x", "text", b"\x00\x01" * 970_201):
        with pytest.raises(ValueError, match="pcm_invalid"):
            media.speaking(pcm, cooperate=lambda: None)
    with pytest.raises(ValueError, match="state_invalid"):
        media.idle("foreign", cooperate=lambda: None)
    assert media._transport.process is None


@pytest.mark.parametrize("field,value", [
    ("frames", True), ("frames", 121), ("sha256", "foreign"), ("originKind", "human"),
    ("classification", "trusted"), ("repeatMode", "loop"), ("mp4", "invalid"),
])
def test_closed_clip_integrity_denies_mutation_or_authority_labels(field, value):
    data = b"\x00\x00\x00\x10ftypisom0000"
    clip = {"mp4": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest(),
            "frames": 2, "repeatMode": "hold_last", "originKind": "generated", "classification": "synthetic"}
    with pytest.raises(ValueError, match="clip_invalid"):
        validate_clip({**clip, field: value}, repeat="hold_last")


def test_real_media_entry_runs_procedural_renderer_and_cpu_ffmpeg_with_owner_progress(tmp_path):
    if os.environ.get("ANANTA_TEST_REAL_COMPANION_MEDIA") != "1":
        pytest.skip("explicit CPU renderer/FFmpeg integration gate not enabled")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.fail("explicit CPU renderer gate requires FFmpeg and ffprobe")
    media = CompanionMediaPreparationProcess(environment={
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "ANANTA_CPU_FALLBACK": "1",
        "MEET_COMPANION_LOG": str(tmp_path / "synthetic-renderer.log"),
        "MEET_AVATAR_SERVICE_ENABLED": "0", "MEET_AVATAR_IDLE_CLIP": str(tmp_path / "missing-idle.mp4"),
    })
    ticks = []
    try:
        idle = media.idle("thinking", cooperate=lambda: ticks.append(True))
        clips = media.speaking(b"\x00\x01" * 4410, cooperate=lambda: ticks.append(True))
        assert idle["frames"] == 24 and len(clips) == 1 and clips[0]["frames"] == 3
        for index, clip in enumerate((idle, *clips)):
            path = tmp_path / f"synthetic-{index}.mp4"
            path.write_bytes(base64.b64decode(clip["mp4"], validate=True))
            probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams",
                                    "-of", "json", str(path)], capture_output=True, timeout=5, check=True)
            streams = json.loads(probe.stdout)["streams"]
            assert len(streams) == 1 and streams[0]["codec_type"] == "video"
            assert streams[0]["codec_name"] == "h264"
            assert (streams[0]["width"], streams[0]["height"]) == (256, 256)
            assert streams[0]["r_frame_rate"] == "12/1" and int(streams[0]["nb_read_frames"]) == clip["frames"]
        assert len(ticks) >= 4
    finally:
        media.close()
    assert media._transport.process.poll() is not None


@pytest.mark.parametrize("response_status", [200, 500, "revoked"])
def test_real_lipsync_http_wait_and_cpu_fallback_keep_owner_active(tmp_path, response_status):
    if os.environ.get("ANANTA_TEST_REAL_COMPANION_MEDIA") != "1":
        pytest.skip("explicit CPU renderer/HTTP integration gate not enabled")
    if not shutil.which("ffmpeg"):
        pytest.fail("explicit fallback gate requires FFmpeg")
    from PIL import Image

    from worker.meet_media.companion_chat_pump import CompanionSessionEnded

    portrait = tmp_path / "synthetic-portrait.png"
    Image.new("RGB", (256, 256)).save(portrait)
    calls = []
    data = b"\x00\x00\x00\x10ftypisom0000"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, set(payload)))
            time.sleep(0.4)
            response = json.dumps({"mp4_b64": base64.b64encode(data).decode(), "frames": 2}).encode()
            try:
                self.send_response(200 if response_status == "revoked" else response_status)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
            except OSError:
                pass  # The revoked owner closes its in-flight connection.

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    media = CompanionMediaPreparationProcess(environment={
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "ANANTA_CPU_FALLBACK": "1",
        "MEET_COMPANION_LOG": str(tmp_path / "synthetic-lipsync.log"), "MEET_AVATAR_SERVICE_ENABLED": "1",
        "MEET_AVATAR_SERVICE_URL": f"http://127.0.0.1:{server.server_port}",
        "MEET_AVATAR_SERVICE_PORTRAIT": str(portrait),
    })
    ticks = []

    def cooperate():
        ticks.append(True)
        if response_status == "revoked" and calls:
            raise CompanionSessionEnded("synthetic_source_revoked")

    try:
        if response_status == "revoked":
            with pytest.raises(CompanionSessionEnded):
                media.speaking(b"\x00\x01" * 4410, cooperate=cooperate)
            assert media._transport.process.poll() is not None
        else:
            clips = media.speaking(b"\x00\x01" * 4410, cooperate=cooperate)
            assert len(clips) == 1 and clips[0]["frames"] == (2 if response_status == 200 else 3)
            assert len(ticks) >= 4
        assert calls == [("/avatar", {"image_png_b64", "audio_wav_b64", "prompt"})]
    finally:
        media.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive() and media._transport.process.poll() is not None
