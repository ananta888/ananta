"""Snake avatar assets: portrait selection for the lip-sync service, idle-clip loop."""

import base64
import hashlib
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from worker.meet_media import avatar_service
from worker.meet_media.avatar_idle_clip import (
    DEFAULT_IDLE_CLIP,
    IDLE_CLIP_ENV,
    MAX_IDLE_CLIP_BYTES,
    IdleClipError,
    IdleClipSource,
    idle_clip_path,
    load_idle_clip,
    mp4_frame_count,
)
from worker.meet_media.avatar_service import (
    DEFAULT_PORTRAIT,
    PORTRAIT_ENV,
    AvatarServiceError,
    LipSyncClient,
    load_portrait,
    portrait_path,
    render,
    wav_bytes,
)
from worker.meet_media.companion_media import SPEECH_RATE
from worker.meet_media.snake_avatar_state import IDLE, LISTENING, SPEAKING, THINKING
from worker.meet_media.snake_avatar_timeline import MAX_CLIP_FRAMES

pytestmark = pytest.mark.timeout(120)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ASSETS = Path(__file__).resolve().parents[1] / "data" / "meet-media" / "worker-state"


# --- synthetic MP4 ---------------------------------------------------------


def box(kind, payload=b""):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def hdlr(handler):
    return box(b"hdlr", b"\x00" * 8 + handler + b"\x00" * 12 + b"\x00")


def stsz(count):
    return box(b"stsz", b"\x00" * 4 + struct.pack(">II", 0, count) + b"\x00\x00\x00\x10" * count)


def trak(handler, count):
    stbl = box(b"stbl", box(b"stsd") + stsz(count))
    return box(b"trak", box(b"tkhd") + box(b"mdia", box(b"mdhd") + hdlr(handler) + box(b"minf", stbl)))


def mp4(frames=36, *, tracks=None, mdat=b"\x00" * 128):
    tracks = [trak(b"vide", frames)] if tracks is None else tracks
    return box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41") + box(b"moov", box(b"mvhd") + b"".join(tracks)) + box(b"mdat", mdat)


class FakeRenderer:
    """Stand-in for ``IdleClips``: records the states it was asked for."""

    def __init__(self):
        self.states = []

    def payload(self, state):
        self.states.append(state)
        return {"mp4": "local", "frames": 24, "repeatMode": "loop", "state": state}


# --- portrait selection -----------------------------------------------------


def test_portrait_path_defaults_to_the_state_asset_and_follows_the_env():
    assert portrait_path({}) == DEFAULT_PORTRAIT == "/state/ananta-snake-portrait.png"
    assert portrait_path({PORTRAIT_ENV: " /tmp/other.png "}) == "/tmp/other.png"
    assert portrait_path({PORTRAIT_ENV: ""}) == DEFAULT_PORTRAIT


def test_load_portrait_returns_the_asset_bytes_verbatim(tmp_path):
    path = tmp_path / "portrait.png"
    path.write_bytes(PNG)
    assert load_portrait(path) == PNG and load_portrait(str(path)) == PNG


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "meet_avatar_service_portrait_missing"),
        (b"", "meet_avatar_service_portrait_invalid"),
        (b"JPEG" + b"\x00" * 32, "meet_avatar_service_portrait_invalid"),
        (PNG + b"\x00" * avatar_service.MAX_PORTRAIT_BYTES, "meet_avatar_service_portrait_invalid"),
    ],
)
def test_load_portrait_never_falls_back_to_a_rendered_image(tmp_path, content, reason):
    path = tmp_path / "portrait.png"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(AvatarServiceError) as info:
        load_portrait(path)
    assert info.value.reason_code == reason and str(path) in info.value.detail


def test_lipsync_client_sends_the_configured_portrait_to_the_service(tmp_path, monkeypatch):
    portrait = PNG + b"new-snake"
    path = tmp_path / "snake.png"
    path.write_bytes(portrait)
    monkeypatch.setenv(PORTRAIT_ENV, str(path))
    sent = []

    def renderer(portrait_png, wav, *, base_url, timeout):
        sent.append(portrait_png)
        return {"mp4": b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16, "frames": 12, "seconds": 0.1, "face_method": "dwpose"}

    logs = []
    client = LipSyncClient(load_portrait(portrait_path()), base_url="http://svc:8189", renderer=renderer, log=logs.append)
    tone = (32767 * 0.3 * np.sin(np.arange(SPEECH_RATE) / 20.0)).astype("<i2").tobytes()
    assert client.clip(tone)["frames"] == 12
    assert sent == [portrait] and any("face_method=dwpose" in line for line in logs)


# --- idle clip --------------------------------------------------------------


def test_idle_clip_path_defaults_to_the_state_asset_and_follows_the_env():
    assert idle_clip_path({}) == DEFAULT_IDLE_CLIP == "/state/ananta-snake-idle.mp4"
    assert idle_clip_path({IDLE_CLIP_ENV: "/tmp/idle.mp4"}) == "/tmp/idle.mp4"


def test_mp4_frame_count_reads_the_video_track_sample_count():
    assert mp4_frame_count(mp4(36)) == 36
    # Audio-only tracks are skipped; the first video track wins.
    assert mp4_frame_count(mp4(tracks=[trak(b"soun", 99), trak(b"vide", 7), trak(b"vide", 8)])) == 7
    for broken, detail in [
        (b"\x00" * 32, "not an mp4"),
        (box(b"ftyp", b"isom") + box(b"mdat"), "no moov"),
        (mp4(tracks=[trak(b"soun", 5)]), "no video track"),
        (box(b"ftyp", b"isom") + struct.pack(">I4s", 500, b"moov"), "box size"),
    ]:
        with pytest.raises(IdleClipError, match="meet_avatar_idle_clip_invalid") as info:
            mp4_frame_count(broken)
        assert info.value.detail == detail


def test_load_idle_clip_is_a_looping_contract_payload(tmp_path):
    data = mp4(36)
    path = tmp_path / "idle.mp4"
    path.write_bytes(data)
    payload = load_idle_clip(path)
    assert payload == {
        "mp4": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest(), "frames": 36,
        "repeatMode": "loop", "originKind": "generated", "classification": "synthetic",
    }


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "meet_avatar_idle_clip_missing"),
        (b"", "meet_avatar_idle_clip_invalid"),
        (mp4(36, mdat=b"\x00" * MAX_IDLE_CLIP_BYTES), "meet_avatar_idle_clip_too_large"),
        (mp4(0), "meet_avatar_idle_clip_frames_invalid"),
        (mp4(MAX_CLIP_FRAMES + 1), "meet_avatar_idle_clip_frames_invalid"),
        (b"RIFF" + b"\x00" * 64, "meet_avatar_idle_clip_invalid"),
    ],
)
def test_load_idle_clip_rejects_unusable_assets(tmp_path, content, reason):
    path = tmp_path / "idle.mp4"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(IdleClipError) as info:
        load_idle_clip(path)
    assert info.value.reason_code == reason


def test_idle_clip_source_serves_the_asset_once_for_every_quiet_state(tmp_path):
    data = mp4(36)
    path = tmp_path / "idle.mp4"
    path.write_bytes(data)
    renderer, logs = FakeRenderer(), []
    source = IdleClipSource(path, fallback=renderer, log=logs.append)
    assert source.source is None
    first = source.payload(IDLE)
    path.unlink()  # cached: later states never touch the file system again
    assert source.payload(THINKING) is first and source.payload(LISTENING) is first
    assert first["repeatMode"] == "loop" and first["frames"] == 36 and base64.b64decode(first["mp4"]) == data
    assert source.source == "asset" and renderer.states == []
    assert logs == ["idle clip %s frames=36 sha256=%s" % (path, hashlib.sha256(data).hexdigest()[:12])]
    with pytest.raises(ValueError, match="meet_avatar_state_invalid"):
        source.payload(SPEAKING)


def test_idle_clip_source_falls_back_to_the_local_renderer_and_logs_once(tmp_path):
    renderer, logs = FakeRenderer(), []
    source = IdleClipSource(tmp_path / "missing.mp4", fallback=renderer, log=logs.append)
    assert source.payload(IDLE)["state"] == IDLE and source.payload(THINKING)["state"] == THINKING
    assert renderer.states == [IDLE, THINKING] and source.source == "fallback"
    assert len(logs) == 1 and logs[0].startswith("idle clip fallback reason=meet_avatar_idle_clip_missing")
    with pytest.raises(IdleClipError, match="meet_avatar_idle_clip_missing"):
        IdleClipSource(tmp_path / "missing.mp4").payload(IDLE)


def test_idle_clip_source_reads_the_path_from_the_environment(tmp_path, monkeypatch):
    path = tmp_path / "env-idle.mp4"
    path.write_bytes(mp4(12))
    monkeypatch.setenv(IDLE_CLIP_ENV, str(path))
    assert IdleClipSource().payload(IDLE)["frames"] == 12


# --- real assets + live service (opt-in by presence / reachability) -----------


def test_shipped_assets_are_contract_conform_when_present():
    portrait, idle = ASSETS / "ananta-snake-portrait.png", ASSETS / "ananta-snake-idle.mp4"
    if not (portrait.is_file() and idle.is_file()):
        pytest.skip("snake assets not present at %s" % ASSETS)
    data = load_portrait(portrait)
    assert struct.unpack(">II", data[16:24]) == (512, 512)
    payload = load_idle_clip(idle)
    assert payload["frames"] == 36 and payload["repeatMode"] == "loop"
    assert len(payload["mp4"]) <= avatar_service.MAX_MP4_BASE64_CHARS


@pytest.mark.integration
def test_live_service_renders_the_shipped_portrait_into_a_contract_clip():
    portrait = ASSETS / "ananta-snake-portrait.png"
    if not portrait.is_file():
        pytest.skip("snake portrait not present at %s" % portrait)
    if os.environ.get(avatar_service.ENABLED_ENV, "1").strip().lower() in ("0", "false", "off", "no"):
        pytest.skip("lip-sync service disabled")
    url = avatar_service.service_url()
    if not avatar_service.health(url, timeout=2):
        pytest.skip("lip-sync service not reachable at %s" % url)
    t = np.arange(2 * SPEECH_RATE) / SPEECH_RATE
    tone = (32767 * 0.3 * np.sin(2 * np.pi * 220.0 * t)).astype("<i2").tobytes()
    result = render(load_portrait(portrait), wav_bytes(tone, SPEECH_RATE), base_url=url, timeout=30)
    assert result["mp4"][4:8] == b"ftyp" and result["width"] == result["height"] == 256 and result["fps"] == 12
    assert 12 <= result["frames"] <= 36
    # How the service located the face is service-side policy (detector or
    # AVATAR_FACE_BOX), not something the worker controls: record, don't assert.
    last = (avatar_service.health_report(url, timeout=5) or {}).get("last_inference") or {}
    print("live face_method=%s cached=%s" % (last.get("face_method"), last.get("avatar_cached")))
    assert last.get("frames") == result["frames"]
