"""Idle avatar loop from a pre-rendered MP4 asset (``persona-video-v1``, loop).

While the companion is not speaking it publishes one looping clip. That clip
is an *asset* (``MEET_AVATAR_IDLE_CLIP``, default
``/state/ananta-snake-idle.mp4``: 3 s, 36 frames, 256x256, 12 fps, seamless at
the wrap) rendered from the same portrait the lip-sync service receives, so
idle and speaking show the same snake. The local procedural renderer
(``companion_media.IdleClips``) is only the fallback when the asset is absent
or unusable.

The frame count is read from the MP4 itself (``stsz`` sample count of the
video track) so the payload's ``frames`` is the truth of the file, not a
constant that can drift from the asset.
"""

import os
import struct
from pathlib import Path

from worker.meet_media.snake_avatar import video_payload
from worker.meet_media.snake_avatar_state import IDLE, LISTENING, THINKING
from worker.meet_media.snake_avatar_timeline import MAX_CLIP_FRAMES

IDLE_CLIP_ENV = "MEET_AVATAR_IDLE_CLIP"
DEFAULT_IDLE_CLIP = "/state/ananta-snake-idle.mp4"
# ``persona-video-v1`` caps the base64 payload at 2 MB (see avatar_service).
MAX_IDLE_CLIP_BYTES = 1_500_000
_CONTAINERS = frozenset({b"moov", b"trak", b"mdia", b"minf", b"stbl"})


class IdleClipError(Exception):
    """Asset failure with a stable ``reason_code`` (never a raw message)."""

    def __init__(self, reason_code, detail=""):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = str(detail)[:200]


def idle_clip_path(environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get(IDLE_CLIP_ENV, "") or DEFAULT_IDLE_CLIP).strip()


def _boxes(data, start, end):
    """Yield ``(type, header_end, box_end)`` for the boxes in ``data[start:end]``."""
    offset = start
    while offset + 8 <= end:
        size, kind = struct.unpack(">I4s", data[offset:offset + 8])
        header = 8
        if size == 1:
            if offset + 16 > end:
                raise IdleClipError("meet_avatar_idle_clip_invalid", "truncated largesize")
            size = struct.unpack(">Q", data[offset + 8:offset + 16])[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            raise IdleClipError("meet_avatar_idle_clip_invalid", "box size")
        yield kind, offset + header, offset + size
        offset += size


def _find(data, start, end, path):
    """First box at ``path`` (tuple of types) inside ``data[start:end]``."""
    for kind, body, box_end in _boxes(data, start, end):
        if kind != path[0]:
            continue
        if len(path) == 1:
            return body, box_end
        found = _find(data, body, box_end, path[1:])
        if found is not None:
            return found
    return None


def mp4_frame_count(data):
    """Sample count of the first video track (``hdlr`` handler ``vide``)."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 16 or data[4:8] != b"ftyp":
        raise IdleClipError("meet_avatar_idle_clip_invalid", "not an mp4")
    moov = _find(data, 0, len(data), (b"moov",))
    if moov is None:
        raise IdleClipError("meet_avatar_idle_clip_invalid", "no moov")
    for kind, body, box_end in _boxes(data, *moov):
        if kind != b"trak":
            continue
        handler = _find(data, body, box_end, (b"mdia", b"hdlr"))
        if handler is None or data[handler[0] + 8:handler[0] + 12] != b"vide":
            continue
        stsz = _find(data, body, box_end, (b"mdia", b"minf", b"stbl", b"stsz"))
        if stsz is None or stsz[1] - stsz[0] < 12:
            raise IdleClipError("meet_avatar_idle_clip_invalid", "no stsz")
        return struct.unpack(">I", data[stsz[0] + 8:stsz[0] + 12])[0]
    raise IdleClipError("meet_avatar_idle_clip_invalid", "no video track")


def load_idle_clip(path):
    """``persona-video-v1`` payload (``repeatMode="loop"``) for the asset at ``path``."""
    try:
        data = Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        raise IdleClipError("meet_avatar_idle_clip_missing", path) from None
    if len(data) > MAX_IDLE_CLIP_BYTES:
        raise IdleClipError("meet_avatar_idle_clip_too_large", path)
    frames = mp4_frame_count(data)
    if not 1 <= frames <= MAX_CLIP_FRAMES:
        raise IdleClipError("meet_avatar_idle_clip_frames_invalid", frames)
    return video_payload(data, frames, repeat="loop")


class IdleClipSource:
    """Looping clip for every non-speaking state, from the asset when possible.

    The asset is read once and cached. ``thinking``/``listening`` reuse the same
    loop: one consistent snake beats a state-specific pose in a different
    drawing style. Without a usable asset ``fallback.payload(state)`` (the local
    renderer) answers instead, and the reason is logged once.
    """

    def __init__(self, path=None, *, fallback=None, log=lambda message: None):
        self.path = idle_clip_path() if path is None else str(path)
        self._fallback = fallback
        self._log = log
        self._payload = None
        self._failed = None

    @property
    def source(self):
        """``"asset"``, ``"fallback"`` or ``None`` before the first request."""
        if self._payload is not None:
            return "asset"
        return "fallback" if self._failed else None

    def payload(self, state=IDLE):
        if state not in (IDLE, LISTENING, THINKING):
            raise ValueError("meet_avatar_state_invalid")
        if self._payload is None and self._failed is None:
            try:
                self._payload = load_idle_clip(self.path)
                self._log(
                    "idle clip %s frames=%s sha256=%s"
                    % (self.path, self._payload["frames"], self._payload["sha256"][:12])
                )
            except IdleClipError as error:
                self._failed = error.reason_code
                self._log("idle clip fallback reason=%s detail=%s" % (error.reason_code, error.detail))
        if self._payload is not None:
            return self._payload
        if self._fallback is None:
            raise IdleClipError(self._failed, self.path)
        return self._fallback.payload(state)
