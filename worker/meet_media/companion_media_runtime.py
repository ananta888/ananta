"""Local media execution only; no Hub, browser, delegation or task queue."""

import json
import sys

from worker.meet_media.companion_media_preparation import (
    IDLE_STATES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    pcm_bytes,
    validate_clip,
    validate_clips,
)


def main():
    # Reuse the configured portrait/idle/lipsync policies inside the owned
    # process; existing broad companion composition remains extraction debt.
    from worker.meet_media.companion import avatar_idle, lipsync_client
    from worker.meet_media.companion_media import AvatarPorts, SpeechAvatarPublisher

    publisher = None
    while True:
        line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            return 0
        try:
            if len(line) > MAX_REQUEST_BYTES or not line.endswith(b"\n"):
                return 2
            request = json.loads(line)
            if not isinstance(request, dict) or set(request) != {"kind", "value"}:
                return 2
            kind, value = request["kind"], request["value"]
            if kind == "idle" and isinstance(value, str) and value in IDLE_STATES:
                result = validate_clip(avatar_idle(value), repeat="loop")
            elif kind == "speaking":
                pcm = pcm_bytes(value)
                if publisher is None:
                    publisher = SpeechAvatarPublisher(AvatarPorts(None, None, None, None), lipsync=lipsync_client())
                _timeline, clips = publisher.prepare(pcm)
                result = validate_clips(clips)
            else:
                return 2
            output = json.dumps({"kind": kind, "result": result}, ensure_ascii=False) + "\n"
            if len(output.encode()) > MAX_RESPONSE_BYTES:
                return 2
            sys.stdout.write(output)
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
