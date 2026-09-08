"""Actual normalized test video/transport; explicitly synthetic profile authority."""

import base64
import copy
import hashlib
import io
import json
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image

from ananta_contracts.meet_persona_video import decode_assignment
from ananta_contracts.persona_video import SanitizedPersonaVideo, encode_video
from tests.meet_dialog_avatar_observer import DialogAvatarObserver
from tests.meet_dialog_image_avatar_scenario import SyntheticImageProfiles


class SyntheticVideoProfiles:
    def __init__(self, wire):
        self.lock, self.revoked = threading.RLock(), False
        content = base64.b64decode(wire["mp4"], validate=True)
        digest = hashlib.sha256(content).hexdigest()
        if digest != wire["sha256"] or wire["frames"] != 12:
            raise ValueError("test_clip_generation_invalid")
        buffer = io.BytesIO()
        Image.new("RGBA", (256, 256), "red").save(buffer, format="PNG")
        preview = buffer.getvalue()
        inspected = SanitizedPersonaVideo(digest, digest, hashlib.sha256(preview).hexdigest(), 12, content, preview)
        self.video = {
            "reference": {
                "tenant_id": "synthetic",
                "project_id": "synthetic",
                "artifact_id": "avatar-video",
                "revision": 1,
                "sha256": digest,
                "kind": "video",
                "classification": "test_only",
            },
            "clip": encode_video(inspected),
            "origin_kind": "generated",
            "repeat_mode": "loop",
        }
        self.pin = {
            "organization_id": "synthetic",
            "owner_kind": "organization",
            "owner_id": "synthetic",
            "selection_digest": digest,
        }
        decode_assignment(self.video, tenant_id="synthetic", project_id="synthetic")

    def select(self, principal, project, selection, purpose):
        with self.lock:
            if (
                self.revoked
                or (principal.subject_id, principal.tenant_id, principal.project_id, project, purpose)
                != ("owner", "synthetic", "synthetic", "synthetic", "publish")
                or selection != self.pin
            ):
                raise PermissionError("synthetic_video_denied")
            return dict(self.video["reference"]), dict(self.pin)

    def require_current(self, principal, project, selection, reference, purpose):
        current, _ = self.select(principal, project, selection, purpose)
        if reference != current:
            raise PermissionError("synthetic_video_reference_changed")

    def prepare(self, principal, project, selection, purpose, *, repeat_mode):
        with self.lock:
            self.select(principal, project, selection, purpose)
            if repeat_mode not in ("loop", "hold_last"):
                raise PermissionError("synthetic_video_repeat_denied")
            return copy.deepcopy(self.video) | {"repeat_mode": repeat_mode}, dict(self.pin)

    def revoke(self):
        with self.lock:
            self.revoked = True


class VideoAvatarScenario:
    def __init__(self, speech, monkeypatch):
        self.observer = DialogAvatarObserver(True, speech, monkeypatch)
        self.profiles = SyntheticImageProfiles()
        repository = Path(__file__).resolve().parents[2] / "webrtc-minimize-server"
        result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {syntheticAvatarVideo} from './test/helpers/machine-avatar-video.mjs'; "
                "process.stdout.write(JSON.stringify(syntheticAvatarVideo()));",
            ],
            cwd=repository,
            check=True,
            capture_output=True,
            timeout=20,
        )
        self.video_profiles = SyntheticVideoProfiles(json.loads(result.stdout))
        self.start_options = {"avatar_images": True, "avatar_videos": True, "duration_seconds": 180}

    def finish(self, app, service, principal, started, speech, command, completed, failures, record_property):
        task_id = started["task_id"]

        def change(*, image=False, enabled=None):
            with app.app_context():
                current = service.inspect(principal, "synthetic", task_id)["controls"]
                if enabled is not None:
                    body = {name: row["enabled"] for name, row in current.items() if name != "revision"}
                    return service.control(
                        principal,
                        "synthetic",
                        task_id,
                        body | {"expected_revision": current["revision"], "avatar": enabled},
                    )
                if image:
                    return service.select_avatar(
                        principal,
                        "synthetic",
                        task_id,
                        {"expected_revision": current["revision"], "profile": self.profiles.catalog["blue"][1]},
                    )
                return service.select_avatar_video(
                    principal,
                    "synthetic",
                    task_id,
                    {
                        "expected_revision": current["revision"],
                        "profile": self.video_profiles.pin,
                        "repeat_mode": "loop",
                    },
                )

        def observed(kind):
            self.observer.wait("open", 12)
            assert command(kind) == (
                {"moving_avatar_video": True} if kind == "avatar_video" else {"moving_avatar_image": "blue"}
            ), {
                "avatar": self.observer.report(),
                "runtime_errors": list(failures),
                "callbacks": speech.callbacks.report(),
            }

        selected = change()
        assert selected["controls"]["avatar"]["enabled"] is False
        assert command("avatar_absent") == {"avatar_absent": True}
        change(enabled=True)
        observed("avatar_video")
        speech.before_question(command)
        assert command("ask") == {"sent": True}
        assert speech.receive_answer(command) == {"received": True}
        change(image=True)
        observed("avatar_image_blue")
        assert command("screen") == {"moving_screen": True}
        speech.require_completed(1, completed, failures)
        speech.require_remote(command)
        change()
        observed("avatar_video")
        change(enabled=False)
        paused_at = time.monotonic()
        self.observer.wait("closed", 3)
        assert command("avatar_absent") == {"avatar_absent": True}
        pause_ms = (time.monotonic() - paused_at) * 1000
        assert pause_ms <= 4000
        change(enabled=True)
        observed("avatar_video")
        self.video_profiles.revoke()
        revoked_at = time.monotonic()
        self.observer.wait("closed", 3)
        local_ms = (time.monotonic() - revoked_at) * 1000
        assert command("avatar_absent") == {"avatar_absent": True}
        remote_ms = (time.monotonic() - revoked_at) * 1000
        assert remote_ms <= 4000
        assert command("screen") == {"moving_screen": True}
        speech.before_question(command)
        assert command("ask") == {"sent": True}
        assert speech.receive_answer(command) == {"received": True}
        speech.require_completed(2, completed, failures)
        speech.require_remote(command)
        assert command("avatar_absent") == {"avatar_absent": True}
        with app.app_context():
            assert service.inspect(principal, "synthetic", task_id, stop=True)["status"] == "cancelled"
        assert completed.wait(10), failures
        assert command("alone") == {"alone": True}
        record_property(
            "dialog_video_avatar",
            {
                "synthetic_policy": True,
                "synthetic_clip": True,
                "actual_gpu": False,
                "production_release_evidence": False,
                "pause_ms": round(pause_ms, 2),
                "local_revoke_ms": round(local_ms, 2),
                "remote_revoke_ms": round(remote_ms, 2),
                "generations": self.observer.generation,
                "speech_samples": speech.samples,
                "remote_audio": speech.remote,
            },
        )
        return True
