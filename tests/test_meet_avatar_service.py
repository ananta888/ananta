"""MuseTalk lip-sync service client: wire format, failure codes, fallback policy."""

import base64
import hashlib
import io
import json
import os
import urllib.error
import wave
from email.message import Message

import numpy as np
import pytest

from worker.meet_media import avatar_service
from worker.meet_media.avatar_service import (
    DEFAULT_URL,
    ENABLED_ENV,
    MAX_CLIP_FRAMES,
    URL_ENV,
    AvatarServiceError,
    LipSyncClient,
    render,
    service_enabled,
    service_url,
    wav_bytes,
)
from worker.meet_media.companion_media import SPEECH_RATE, AvatarPorts, SpeechAvatarPublisher
from worker.meet_media.snake_avatar import PORTRAIT_SIZE, portrait_png, render_portrait
from worker.meet_media.snake_avatar_timeline import SpeechMediaTimeline

pytestmark = pytest.mark.timeout(120)
PORTRAIT = portrait_png()
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64


def tone_pcm(seconds, hz=220.0):
    t = np.arange(int(seconds * SPEECH_RATE)) / SPEECH_RATE
    return (32767 * 0.3 * np.sin(2 * np.pi * hz * t)).astype("<i2").tobytes()


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    """Scripted service: each entry is a status/body pair or an exception."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        entry = self.script.pop(0)
        if isinstance(entry, Exception):
            raise entry
        status, body, headers = entry
        if status >= 400:
            headers = FakeResponse(status, body, headers).headers
            raise urllib.error.HTTPError(request.full_url, status, "error", headers, io.BytesIO(body))
        return FakeResponse(status, body, headers)


def ok_body(frames=24, mp4=MP4):
    document = {"mp4_b64": base64.b64encode(mp4).decode(), "frames": frames, "fps": 12, "width": 256, "height": 256}
    return json.dumps(document).encode()


# --- portrait -------------------------------------------------------------


def test_portrait_is_square_deterministic_and_keeps_the_mouth_in_the_lower_half():
    image = render_portrait()
    assert image.size == (PORTRAIT_SIZE, PORTRAIT_SIZE) and image.mode == "RGB"
    assert portrait_png() == PORTRAIT and PORTRAIT[:8] == b"\x89PNG\r\n\x1a\n"
    pixels = np.asarray(image)
    outline = np.all(np.abs(pixels.astype(int) - np.array([0x25, 0x5C, 0x33])) < 8, axis=2)
    # Mouth arc (outline colour inside the head, x 172..340) sits below 55 % height.
    rows = np.where(outline[:, 200:312].any(axis=1))[0]
    assert rows.min() < PORTRAIT_SIZE * 0.2 and (rows > PORTRAIT_SIZE * 0.55).any()
    with pytest.raises(ValueError, match="meet_avatar_portrait_size_invalid"):
        render_portrait(64)


# --- wire format ----------------------------------------------------------


def test_wav_bytes_wraps_pcm_without_resampling():
    pcm = tone_pcm(0.5)
    data = wav_bytes(pcm, SPEECH_RATE)
    with wave.open(io.BytesIO(data)) as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, SPEECH_RATE)
        assert reader.readframes(reader.getnframes()) == pcm
    with pytest.raises(AvatarServiceError, match="meet_avatar_service_audio_invalid"):
        wav_bytes(pcm + b"\x00", SPEECH_RATE)


def test_render_posts_plain_base64_and_returns_mp4_with_metadata():
    opener = FakeOpener([(200, ok_body(frames=24), {})])
    wav = wav_bytes(tone_pcm(2.0), SPEECH_RATE)
    result = render(PORTRAIT, wav, base_url="http://svc:8189/", opener=opener, timeout=5)
    request, timeout = opener.requests[0]
    assert request.full_url == "http://svc:8189/avatar" and request.get_method() == "POST" and 0 < timeout <= 5
    body = json.loads(request.data)
    assert set(body) == {"image_png_b64", "audio_wav_b64", "prompt"} and body["prompt"] == ""
    assert base64.b64decode(body["image_png_b64"], validate=True) == PORTRAIT
    assert base64.b64decode(body["audio_wav_b64"], validate=True)[:4] == b"RIFF"
    assert result["mp4"] == MP4 and result["frames"] == 24 and result["fps"] == 12
    assert result["sha256"] == hashlib.sha256(MP4).hexdigest() and result["width"] == result["height"] == 256
    assert isinstance(result["seconds"], float)


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ([(422, b'{"detail":"bad audio"}', {})], "meet_avatar_service_rejected"),
        ([(500, b"boom", {})], "meet_avatar_service_failed"),
        ([(429, b"", {"Retry-After": "1"}), (429, b"", {"Retry-After": "1"})], "meet_avatar_service_busy"),
        ([urllib.error.URLError(ConnectionRefusedError(111, "refused"))], "meet_avatar_service_unreachable"),
        ([urllib.error.URLError(TimeoutError("timed out"))], "meet_avatar_service_timeout"),
        ([TimeoutError("timed out")], "meet_avatar_service_timeout"),
        ([(200, b"not json", {})], "meet_avatar_service_response_invalid"),
        ([(200, ok_body(mp4=b"\x00" * 40), {})], "meet_avatar_service_clip_invalid"),
        ([(200, ok_body(frames=MAX_CLIP_FRAMES + 1), {})], "meet_avatar_service_frames_invalid"),
        ([(200, ok_body(frames=0), {})], "meet_avatar_service_frames_invalid"),
    ],
)
def test_render_maps_every_failure_to_a_reason_code(script, reason):
    slept = []
    with pytest.raises(AvatarServiceError) as info:
        render(
            PORTRAIT, wav_bytes(tone_pcm(1.0), SPEECH_RATE),
            base_url="http://svc:8189", opener=FakeOpener(script), timeout=5, sleep=slept.append,
        )
    assert info.value.reason_code == reason and str(info.value) == reason
    if reason == "meet_avatar_service_busy":
        assert slept == [1.0]


def test_render_retries_once_after_429_within_the_deadline():
    opener = FakeOpener([(429, b"", {"Retry-After": "7"}), (200, ok_body(), {})])
    slept = []
    result = render(
        PORTRAIT, wav_bytes(tone_pcm(1.0), SPEECH_RATE),
        base_url="http://svc:8189", opener=opener, timeout=5, sleep=slept.append,
    )
    assert result["frames"] == 24 and len(opener.requests) == 2
    # Retry-After is bounded so a misbehaving service cannot stall the reply.
    assert slept == [avatar_service.MAX_RETRY_AFTER_SECONDS]


def test_render_rejects_oversized_clips_and_invalid_inputs():
    huge = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 1_600_000
    with pytest.raises(AvatarServiceError, match="meet_avatar_service_clip_too_large"):
        opener = FakeOpener([(200, ok_body(mp4=huge), {})])
        render(PORTRAIT, wav_bytes(tone_pcm(1.0), SPEECH_RATE), base_url="http://svc:8189", opener=opener)
    with pytest.raises(AvatarServiceError, match="meet_avatar_service_portrait_invalid"):
        render(b"JPEG", wav_bytes(tone_pcm(1.0), SPEECH_RATE), base_url="http://svc:8189", opener=FakeOpener([]))
    with pytest.raises(AvatarServiceError, match="meet_avatar_service_audio_invalid"):
        render(PORTRAIT, b"\x00" * 10, base_url="http://svc:8189", opener=FakeOpener([]))


def test_environment_switches():
    assert service_url({}) == DEFAULT_URL and service_url({URL_ENV: "http://10.0.0.5:9000/"}) == "http://10.0.0.5:9000"
    with pytest.raises(AvatarServiceError, match="meet_avatar_service_url_invalid"):
        service_url({URL_ENV: "ftp://x"})
    assert service_enabled({}) is True
    assert service_enabled({ENABLED_ENV: "0"}) is False and service_enabled({ENABLED_ENV: "off"}) is False


# --- fallback policy ------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_lipsync_client_returns_contract_payload_and_falls_back_quietly_after_failure():
    clock = FakeClock()
    calls = []
    outcomes = [{"mp4": MP4, "frames": 24, "seconds": 0.1}, AvatarServiceError("meet_avatar_service_unreachable")]

    def renderer(portrait, wav, *, base_url, timeout):
        calls.append((portrait, wav[:4], base_url, timeout))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    logs = []
    client = LipSyncClient(
        PORTRAIT, base_url="http://svc:8189", timeout=7, cooldown=30, renderer=renderer, clock=clock, log=logs.append
    )
    payload = client.clip(tone_pcm(2.0))
    assert payload == {
        "mp4": base64.b64encode(MP4).decode(), "sha256": hashlib.sha256(MP4).hexdigest(), "frames": 24,
        "repeatMode": "hold_last", "originKind": "generated", "classification": "synthetic",
    }
    assert calls[0] == (PORTRAIT, b"RIFF", "http://svc:8189", 7.0)
    assert client.clip(tone_pcm(2.0)) is None and client.last_error == "meet_avatar_service_unreachable"
    # Cool-down: no further round trip until it expires, then one is allowed again.
    assert client.clip(tone_pcm(2.0)) is None and len(calls) == 2 and client.available is False
    clock.now += 30
    outcomes.append({"mp4": MP4, "frames": 24, "seconds": 0.1})
    assert client.available is True and client.clip(tone_pcm(2.0))["frames"] == 24 and len(calls) == 3
    assert (client.rendered, client.failed) == (2, 1)
    assert any("lipsync fallback reason=meet_avatar_service_unreachable" in line for line in logs)


def test_lipsync_client_refuses_audio_longer_than_the_service_limit():
    client = LipSyncClient(PORTRAIT, base_url="http://svc:8189", renderer=lambda *a, **k: pytest.fail("must not call"))
    assert client.clip(tone_pcm(10.5)) is None and client.available is True


def fake_encoder(raw_path, video_path, frames):
    video_path.write_bytes(b"\x00\x00\x00\x18ftypisom" + frames.to_bytes(4, "big"))


def test_publisher_uses_service_clips_per_segment_and_local_clips_where_it_fails():
    pcm = tone_pcm(23.0)
    timeline = SpeechMediaTimeline(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0, SPEECH_RATE)
    segments = timeline.segments()
    assert len(segments) == 3
    windows = []

    def lipsync(segment_pcm):
        windows.append(len(segment_pcm) // 2)
        if len(windows) == 2:
            return None  # simulated service failure on the middle segment
        frames = len(segment_pcm) // 2 * 12 // SPEECH_RATE
        return {"mp4": base64.b64encode(MP4).decode(), "sha256": hashlib.sha256(MP4).hexdigest(), "frames": frames,
                "repeatMode": "hold_last", "originKind": "generated", "classification": "synthetic"}

    logs = []
    ports = AvatarPorts(None, None, None, None)
    publisher = SpeechAvatarPublisher(ports, encoder=fake_encoder, log=logs.append, lipsync=lipsync)
    _timeline, clips = publisher.prepare(pcm)
    # Every segment is sent with exactly its own audio window (<= 10 s).
    assert windows == [segment.end_sample - segment.start_sample for segment in segments]
    assert all(window <= 10 * SPEECH_RATE for window in windows)
    assert [base64.b64decode(clip["mp4"]) == MP4 for clip in clips] == [True, False, True]
    assert clips[1]["frames"] == segments[1].frames and clips[1]["repeatMode"] == "hold_last"
    assert all(clip["originKind"] == "generated" and clip["classification"] == "synthetic" for clip in clips)


def test_publisher_treats_a_raising_lipsync_port_as_fallback():
    def lipsync(_pcm):
        raise RuntimeError("boom")

    logs = []
    ports = AvatarPorts(None, None, None, None)
    publisher = SpeechAvatarPublisher(ports, encoder=fake_encoder, log=logs.append, lipsync=lipsync)
    _timeline, clips = publisher.prepare(tone_pcm(1.0))
    assert len(clips) == 1 and clips[0]["frames"] == 12 and any("lipsync_err" in line for line in logs)


# --- live service (opt-in by reachability) --------------------------------


@pytest.mark.integration
def test_live_service_renders_a_contract_clip_for_two_seconds_of_speech():
    if os.environ.get(ENABLED_ENV, "1").strip().lower() in ("0", "false", "off", "no"):
        pytest.skip("lip-sync service disabled")
    url = service_url()
    if not avatar_service.health(url, timeout=2):
        pytest.skip("lip-sync service not reachable at %s" % url)
    result = render(PORTRAIT, wav_bytes(tone_pcm(2.0), SPEECH_RATE), base_url=url, timeout=30)
    assert result["mp4"][4:8] == b"ftyp" and result["fps"] == 12 and result["width"] == result["height"] == 256
    assert 12 <= result["frames"] <= 36 and len(base64.b64encode(result["mp4"])) <= 2_000_000
