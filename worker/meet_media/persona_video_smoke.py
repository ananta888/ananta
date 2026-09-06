"""Synthetic renderer-only NVENC observation; no room, user image or release claim."""

import base64
import io
import json
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from PIL import Image

from worker.meet_media.persona_image import sanitize_image
from worker.meet_media.persona_video import persona_video


def probe():
    original = io.BytesIO()
    Image.new("RGB", (48, 32), "red").save(original, format="PNG")
    inspected = sanitize_image(original.getvalue(), "image/png")
    assignment = {
        "tenant_id": "synthetic-probe",
        "project_id": "synthetic-probe",
        "persona_image": {
            "reference": {
                "tenant_id": "synthetic-probe",
                "project_id": "synthetic-probe",
                "artifact_id": "synthetic-image",
                "revision": 1,
                "sha256": inspected.image_sha256,
                "kind": "image",
                "classification": "test_only",
            },
            "png": base64.b64encode(inspected.png).decode(),
        },
    }
    deadline = time.monotonic() + 35

    def synthetic_checkpoint():
        if time.monotonic() >= deadline:
            raise ValueError("synthetic_persona_probe_expired")

    with tempfile.TemporaryDirectory(prefix="persona-video-smoke-") as temporary:
        directory = Path(temporary)
        audio = directory / "silence.wav"
        with wave.open(str(audio), "wb") as output:
            output.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            output.writeframes(b"\0\0" * 22050)
        video = persona_video(assignment, audio, 1, directory, require_current=synthetic_checkpoint)
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,r_frame_rate",
                "-of",
                "json",
                str(video),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        stream = json.loads(result.stdout)["streams"][0]
        if stream != {"codec_name": "h264", "width": 256, "height": 256, "r_frame_rate": "12/1"}:
            raise ValueError("synthetic_persona_video_format_mismatch")
    return {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "renderer": "persona-image-h264_nvenc",
        "production_release_evidence": False,
    }


if __name__ == "__main__":
    print(json.dumps(probe()))
