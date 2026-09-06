"""Compose exactly one Hub-delegated response; never create or route tasks."""

import base64
import json
import sys
import tempfile
from pathlib import Path

from worker.meet_media.avatar import avatar
from worker.meet_media.llm import answer
from worker.meet_media.speech import speech


def run(turn):
    with tempfile.TemporaryDirectory(prefix="meet-turn-") as temporary:
        directory = Path(temporary)
        reply = answer(turn["text"])
        wav = directory / "speech.wav"
        samples, rate, duration = speech(reply, wav)
        video = avatar(wav, samples, rate, duration, directory)
        result = {
            "text": reply,
            "audio": {"mime": "audio/wav", "base64": base64.b64encode(wav.read_bytes()).decode()},
            "video": {"mime": "video/mp4", "base64": base64.b64encode(video.read_bytes()).decode()},
            "duration_seconds": round(duration, 3),
            "engines": {"llm": "ollama", "speech": "piper-cuda", "video": "procedural-avatar-h264_nvenc"},
        }
        if "meeting" in turn:
            from worker.meet_media.lease_guard import HubLeaseGuard
            from worker.meet_media.publisher import publish

            result["meeting"] = publish(
                turn["meeting"], reply, video, turn["deadline"], HubLeaseGuard(turn["task_id"], turn["lease_id"])
            )
        return result


if __name__ == "__main__":
    try:
        result = run(json.load(sys.stdin))
        sys.stdout.write(json.dumps(result))
    except Exception:
        # Model/request content, provider responses and filesystem details are private.
        sys.stderr.write("meet_local_media_execution_failed\n")
        sys.exit(1)
