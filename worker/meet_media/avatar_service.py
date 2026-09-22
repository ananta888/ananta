"""Client for the local MuseTalk lip-sync service (portrait + WAV -> MP4 clip).

The service turns one reference portrait and up to 10 s of speech into a
muted, video-only H.264 clip that already satisfies ``persona-video-v1``
(256x256, 12 fps, <= 120 frames). This module is the *only* place that knows
the wire format; timing and publication stay in ``companion_media``.

Two layers (SRP):

* ``render``        – one bounded HTTP round trip, every failure becomes an
                      ``AvatarServiceError`` with a stable ``reason_code``.
* ``LipSyncClient`` – the fallback policy the companion needs: a portrait,
                      one bounded retry on 429, a cool-down after failures so
                      an unreachable service costs one timeout, not one per
                      clip, and ``persona-video-v1`` payloads via
                      ``snake_avatar.video_payload``.
"""

import base64
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request
import wave
from urllib.parse import urlsplit

from worker.meet_media.snake_avatar import video_payload
from worker.meet_media.snake_avatar_timeline import MAX_CLIP_FRAMES

DEFAULT_URL = "http://172.18.112.1:8189"
URL_ENV = "MEET_AVATAR_SERVICE_URL"
ENABLED_ENV = "MEET_AVATAR_SERVICE_ENABLED"
# Connect + response deadline for one clip (<= 10 s of audio). Measured
# inference on the reference GPU is a few seconds; anything slower would
# delay the reply more than the local renderer does.
DEFAULT_TIMEOUT_SECONDS = 20.0
HEALTH_TIMEOUT_SECONDS = 3.0
# The service serializes inference; a 429 while a previous clip is still
# rendering is retried once after its (bounded) Retry-After.
MAX_RETRY_AFTER_SECONDS = 2.0
# Matches the service's "<= 2 MB base64" promise and the client contract.
MAX_MP4_BASE64_CHARS = 2_000_000
MAX_AUDIO_SECONDS = 10.0
MAX_PORTRAIT_BYTES = 4 * 1024 * 1024
_FALSE = frozenset({"0", "false", "off", "no"})


class AvatarServiceError(Exception):
    """Service failure with a stable ``reason_code`` (never a raw message)."""

    def __init__(self, reason_code, detail=""):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = str(detail)[:200]


def service_url(environ=None):
    environ = os.environ if environ is None else environ
    value = str(environ.get(URL_ENV, "") or DEFAULT_URL).strip()
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path not in ("", "/"):
        raise AvatarServiceError("meet_avatar_service_url_invalid")
    return value.rstrip("/")


def service_enabled(environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get(ENABLED_ENV, "1")).strip().lower() not in _FALSE


def wav_bytes(pcm_s16le, rate):
    """Wrap mono s16le PCM in a WAV container (the service resamples itself)."""
    if type(rate) is not int or not 8_000 <= rate <= 48_000 or len(pcm_s16le) % 2:
        raise AvatarServiceError("meet_avatar_service_audio_invalid")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(bytes(pcm_s16le))
    return buffer.getvalue()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, response, *_args, **_kwargs):
        response.close()
        raise AvatarServiceError("meet_avatar_service_redirect_denied")


def _open(request, timeout, opener=None):
    opener = urllib.request.build_opener(_NoRedirect()) if opener is None else opener
    return opener.open(request, timeout=timeout)


def _post_json(url, body, *, timeout, opener=None):
    """Return (status, headers, bytes); HTTP errors are returned, not raised."""
    request = urllib.request.Request(url, body, {"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with _open(request, timeout, opener) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        payload = error.read() if hasattr(error, "read") else b""
        return error.code, error.headers, payload
    except (TimeoutError, urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise AvatarServiceError("meet_avatar_service_timeout", reason) from None
        raise AvatarServiceError("meet_avatar_service_unreachable", reason) from None


def _retry_after(headers):
    try:
        value = float(headers.get("Retry-After", "1") if headers is not None else 1)
    except (TypeError, ValueError):
        value = 1.0
    return min(MAX_RETRY_AFTER_SECONDS, max(0.0, value))


def _decode(payload):
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AvatarServiceError("meet_avatar_service_response_invalid") from None
    if not isinstance(document, dict) or not isinstance(document.get("mp4_b64"), str):
        raise AvatarServiceError("meet_avatar_service_response_invalid")
    encoded = document["mp4_b64"]
    if not 0 < len(encoded) <= MAX_MP4_BASE64_CHARS:
        raise AvatarServiceError("meet_avatar_service_clip_too_large")
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise AvatarServiceError("meet_avatar_service_response_invalid") from None
    if len(data) < 16 or data[4:8] != b"ftyp":
        raise AvatarServiceError("meet_avatar_service_clip_invalid")
    frames = document.get("frames")
    if type(frames) is not int or not 1 <= frames <= MAX_CLIP_FRAMES:
        raise AvatarServiceError("meet_avatar_service_frames_invalid")
    return {
        "mp4": data,
        "sha256": hashlib.sha256(data).hexdigest(),
        "frames": frames,
        "fps": document.get("fps"),
        "width": document.get("width"),
        "height": document.get("height"),
    }


def render(portrait_png_bytes, wav_bytes_, *, base_url, timeout=DEFAULT_TIMEOUT_SECONDS, opener=None, sleep=time.sleep):
    """One lip-sync clip for ``wav_bytes_`` spoken by the ``portrait_png_bytes`` face.

    Returns ``{"mp4", "sha256", "frames", "fps", "width", "height", "seconds"}``.
    Raises ``AvatarServiceError`` with ``reason_code`` in
    ``meet_avatar_service_{busy,rejected,failed,timeout,unreachable,
    response_invalid,clip_invalid,clip_too_large,frames_invalid,...}``.
    """
    if not isinstance(portrait_png_bytes, (bytes, bytearray)) or not 0 < len(portrait_png_bytes) <= MAX_PORTRAIT_BYTES:
        raise AvatarServiceError("meet_avatar_service_portrait_invalid")
    if portrait_png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise AvatarServiceError("meet_avatar_service_portrait_invalid")
    if not isinstance(wav_bytes_, (bytes, bytearray)) or len(wav_bytes_) < 44 or wav_bytes_[:4] != b"RIFF":
        raise AvatarServiceError("meet_avatar_service_audio_invalid")
    body = json.dumps(
        {
            "image_png_b64": base64.b64encode(bytes(portrait_png_bytes)).decode("ascii"),
            "audio_wav_b64": base64.b64encode(bytes(wav_bytes_)).decode("ascii"),
            "prompt": "",
        }
    ).encode("utf-8")
    url = base_url.rstrip("/") + "/avatar"
    started = time.monotonic()
    deadline = started + float(timeout)
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AvatarServiceError("meet_avatar_service_timeout")
        status, headers, payload = _post_json(url, body, timeout=remaining, opener=opener)
        if status == 200:
            result = _decode(payload)
            result["seconds"] = round(time.monotonic() - started, 3)
            return result
        if status == 429 and attempt == 0:
            wait = _retry_after(headers)
            if time.monotonic() + wait >= deadline:
                raise AvatarServiceError("meet_avatar_service_busy")
            sleep(wait)
            continue
        if status == 429:
            raise AvatarServiceError("meet_avatar_service_busy")
        if status == 422:
            raise AvatarServiceError("meet_avatar_service_rejected", payload[:200])
        raise AvatarServiceError("meet_avatar_service_failed", "http %s" % status)
    raise AvatarServiceError("meet_avatar_service_busy")


def health(base_url, *, timeout=HEALTH_TIMEOUT_SECONDS, opener=None):
    """``True`` when the service answers ``/health`` with ``ready: true``."""
    request = urllib.request.Request(base_url.rstrip("/") + "/health", headers={"Accept": "application/json"})
    try:
        with _open(request, timeout, opener) as response:
            document = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, AvatarServiceError):
        return False
    return isinstance(document, dict) and document.get("ready") is True


class LipSyncClient:
    """Speaking clips from the service, with an explicit fallback policy.

    ``clip`` returns a ``persona-video-v1`` payload or ``None``; callers fall
    back to the local renderer on ``None`` and never see transport errors.
    After a failure the client stays quiet for ``cooldown`` seconds so an
    unreachable service does not add one timeout per clip segment.
    """

    def __init__(
        self, portrait_png, *, base_url=None, timeout=DEFAULT_TIMEOUT_SECONDS, cooldown=30.0,
        rate=22050, renderer=render, clock=time.monotonic, log=lambda message: None,
    ):
        self.portrait_png = bytes(portrait_png)
        self.base_url = service_url() if base_url is None else base_url
        self.timeout = float(timeout)
        self.cooldown = float(cooldown)
        self.rate = int(rate)
        self._render = renderer
        self._clock = clock
        self._log = log
        self._quiet_until = 0.0
        self.last_error = None
        self.rendered = 0
        self.failed = 0

    @property
    def available(self):
        return self._clock() >= self._quiet_until

    def __call__(self, pcm_s16le):
        """The ``lipsync`` port of ``SpeechAvatarPublisher``."""
        return self.clip(pcm_s16le)

    def clip(self, pcm_s16le, *, repeat="hold_last"):
        if not self.available:
            return None
        seconds = len(pcm_s16le) / 2 / self.rate
        if seconds > MAX_AUDIO_SECONDS + 1e-6:
            # The service truncates at 10 s; the caller must segment first.
            self._log("lipsync skip reason=meet_avatar_service_audio_too_long seconds=%.2f" % seconds)
            return None
        try:
            result = self._render(
                self.portrait_png, wav_bytes(pcm_s16le, self.rate), base_url=self.base_url, timeout=self.timeout
            )
        except AvatarServiceError as error:
            self.failed += 1
            self.last_error = error.reason_code
            self._quiet_until = self._clock() + self.cooldown
            self._log("lipsync fallback reason=%s detail=%s" % (error.reason_code, error.detail))
            return None
        self.rendered += 1
        self._log(
            "lipsync clip frames=%s bytes=%s seconds=%s" % (result["frames"], len(result["mp4"]), result.get("seconds"))
        )
        return video_payload(result["mp4"], result["frames"], repeat=repeat)
