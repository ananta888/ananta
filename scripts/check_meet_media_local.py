#!/usr/bin/env python3
"""Real GPU technical observation, not Hub Registry production evidence.

Run against only the explicitly supplied private worker. Persists synthetic
demo outputs only when --output is passed; no model weights or secrets logged.
"""

import argparse
import base64
import io
import json
import sys
import time
import uuid
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.services.meet_media_transport import HttpMediaWorker
from worker.meet_media.contract import load_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    result = HttpMediaWorker(args.endpoint, load_key(args.key_file)).execute(
        {
            "schema": "ananta.meet-turn.v1",
            "task_id": str(uuid.uuid4()),
            "lease_id": str(uuid.uuid4()),
            "tenant_id": "synthetic-local-check",
            "project_id": "synthetic-local-check",
            "deadline": int(time.time()) + 115,
            "text": "Stelle dich bitte in einem kurzen Satz als lokaler KI-Meetingassistent vor.",
        }
    )
    wav = base64.b64decode(result["audio"]["base64"])
    with wave.open(io.BytesIO(wav)) as audio:
        frames = audio.readframes(audio.getnframes())
        if audio.getnframes() < 1000 or not any(frames):
            raise ValueError("generated_audio_empty")
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        for field, suffix in (("audio", "wav"), ("video", "mp4")):
            with (args.output / f"demo.{suffix}").open("xb") as output:
                output.write(base64.b64decode(result[field]["base64"]))
    print(
        json.dumps(
            {
                "status": "passed",
                "classification": "synthetic_technical_observation",
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "duration_seconds": result["duration_seconds"],
                "engines": result["engines"],
                "text": result["text"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
