"""Actual transport with deterministic images and explicitly synthetic approval."""

import base64
import copy
import hashlib
import io
import re
import threading
import time

from PIL import Image

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_service import MeetDialogService
from tests.meet_dialog_avatar_observer import DialogAvatarObserver
from worker.meet_media.dialog_speech_output import DialogSpeechOutput, speech_binding
from worker.meet_media.speech_publication import SpeechPublication


class SyntheticImageProfiles:
    """Small pre-authorized catalog; never a production profile/evidence issuer."""

    def __init__(self):
        self.lock = threading.RLock()
        self.revoked = False
        self.catalog = {}
        for name, color in (("red", (220, 20, 20, 255)), ("blue", (20, 20, 220, 255))):
            stream = io.BytesIO()
            Image.new("RGBA", (8, 8), color).save(stream, format="PNG")
            content = stream.getvalue()
            digest = hashlib.sha256(content).hexdigest()
            pin = {
                "organization_id": "synthetic",
                "owner_kind": "organization",
                "owner_id": "synthetic",
                "selection_digest": digest,
            }
            image = {
                "reference": {
                    "tenant_id": "synthetic",
                    "project_id": "synthetic",
                    "artifact_id": "avatar-" + name,
                    "revision": 1,
                    "sha256": digest,
                    "kind": "image",
                    "classification": "test_only",
                },
                "png": base64.b64encode(content).decode(),
            }
            self.catalog[name] = (image, pin)

    def prepare(self, principal, project, selection, purpose):
        with self.lock:
            if self.revoked or (principal.subject_id, principal.tenant_id, principal.project_id, project, purpose) != (
                "owner",
                "synthetic",
                "synthetic",
                "synthetic",
                "publish",
            ):
                raise PermissionError("synthetic_avatar_denied")
            for image, pin in self.catalog.values():
                if selection == pin:
                    return copy.deepcopy(image), dict(pin)
            raise PermissionError("synthetic_avatar_pin_unknown")

    def select(self, principal, project, selection, purpose):
        image, pin = self.prepare(principal, project, selection, purpose)
        return image["reference"], pin

    def require_current(self, principal, project, selection, reference, purpose):
        image, _ = self.prepare(principal, project, selection, purpose)
        if image["reference"] != reference:
            raise PermissionError("synthetic_avatar_reference_changed")

    def revoke(self):
        with self.lock:
            self.revoked = True


class ImageAvatarScenario:
    def __init__(self, speech, monkeypatch, *, renewal=False):
        self.observer = DialogAvatarObserver(True, speech, monkeypatch)
        self.profiles = SyntheticImageProfiles()
        self.start_options = {"avatar_images": True}
        self.renewal = None
        if renewal:
            from tests.meet_avatar_renewal_observer import AvatarRenewalObserver

            self.renewal = AvatarRenewalObserver(monkeypatch)
            self.start_options["duration_seconds"] = 180
        self.callback_errors = []
        self.source_errors = []
        exchange = MeetDialogService.exchange

        def observe_exchange(service, payload):
            try:
                return exchange(service, payload)
            except MeetError as error:
                self.callback_errors.append({"code": error.code, "status": error.status})
                raise

        monkeypatch.setattr(MeetDialogService, "exchange", observe_exchange)
        writable = SpeechPublication.writable_samples

        def observe_writable(publication):
            try:
                return writable(publication)
            except Exception as error:
                codes = re.findall(r"\bmeet_[a-z_]{1,64}\b", str(error))
                self.source_errors.append(codes[0] if codes else type(error).__name__)
                raise

        monkeypatch.setattr(SpeechPublication, "writable_samples", observe_writable)
        require_current = DialogSpeechOutput.require_current

        def observe_authority(output):
            try:
                return require_current(output)
            except Exception as error:
                changed = []
                if output.binding is not None:
                    try:
                        current = speech_binding(
                            output.assignment, output.receipt, output.controls, output.binding["sender_peer_id"]
                        )
                        changed = [key for key in current if current[key] != output.binding[key]]
                    except ValueError:
                        changed = ["binding_denied"]
                codes = re.findall(r"\bmeet_[a-z_]{1,64}\b", str(error))
                self.source_errors.append(
                    {
                        "code": codes[0] if codes else type(error).__name__,
                        "changed_fields": changed,
                        "fresh": output.monotonic() < output.fresh_until,
                    }
                )
                raise

        monkeypatch.setattr(DialogSpeechOutput, "require_current", observe_authority)

    def finish(self, app, service, principal, started, speech, command, completed, failures, record_property):
        task_id = started["task_id"]

        def select(color):
            with app.app_context():
                current = service.inspect(principal, "synthetic", task_id)
                return service.select_avatar(
                    principal,
                    "synthetic",
                    task_id,
                    {
                        "expected_revision": current["controls"]["revision"],
                        "profile": self.profiles.catalog[color][1],
                    },
                )

        def moving(color):
            value = command("avatar_image_" + color)
            assert value == {"moving_avatar_image": color}, {"remote": value, "runtime_errors": failures}

        selected = select("red")
        assert selected["controls"]["avatar"]["enabled"] is False
        assert command("avatar_absent") == {"avatar_absent": True}
        with app.app_context():
            controls = selected["controls"]
            body = {name: row["enabled"] for name, row in controls.items() if name != "revision"}
            service.control(
                principal,
                "synthetic",
                task_id,
                body
                | {
                    "expected_revision": controls["revision"],
                    "avatar": True,
                },
            )
        try:
            before = self.observer.wait("open", 12)
        except AssertionError as error:
            raise AssertionError(
                {
                    "avatar": str(error),
                    "runtime_errors": list(failures),
                    "hub_callbacks": self.callback_errors,
                    "recent_callbacks": speech.callbacks.report(),
                    "slow_rpc": speech.rpc.report(),
                }
            ) from error
        moving("red")
        speech.before_question(command)
        assert command("ask") == {"sent": True}
        assert speech.receive_answer(command) == {"received": True}
        assert not speech.samples, "image switch must start before local speech completion"
        select("blue")
        with self.observer.condition:
            assert self.observer.condition.wait_for(
                lambda: self.observer.state == "open" and self.observer.generation > before, timeout=12
            ), {
                "avatar_state": self.observer.state,
                "runtime_errors": list(failures),
                "control_timing": speech.control_reads.report(),
                "callbacks": speech.callbacks.report(),
                "slow_rpc": speech.rpc.report(),
            }
        moving("blue")
        assert command("screen") == {"moving_screen": True}
        try:
            speech.require_completed(1, completed, failures)
        except AssertionError as error:
            raise AssertionError(
                {"speech": str(error), "hub_callbacks": self.callback_errors, "source_errors": self.source_errors}
            ) from error
        speech.require_remote(command)
        if self.renewal is not None:
            self.renewal.require_renewed(self.profiles.catalog["blue"][0]["reference"]["sha256"])
            self.observer.wait("open", 12)
            moving("blue")
            assert len(speech.samples) == 1  # Never replay the old reply after renewal.
            assert command("screen") == {"moving_screen": True}
        self.profiles.revoke()
        revoked_at = time.monotonic()
        self.observer.wait("closed", 3)
        local_ms = (time.monotonic() - revoked_at) * 1000
        assert command("avatar_absent") == {"avatar_absent": True}
        remote_ms = (time.monotonic() - revoked_at) * 1000
        assert remote_ms <= 4000
        assert command("screen") == {"moving_screen": True}
        # A profile denial must neither end the parent nor disable voice/chat.
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
            "dialog_image_avatar",
            {
                "synthetic_policy": True,
                "synthetic_images": True,
                "synthetic_audio": True,
                "actual_gpu": False,
                "production_release_evidence": False,
                "local_revoke_ms": round(local_ms, 2),
                "remote_revoke_ms": round(remote_ms, 2),
                "generations": self.observer.generation,
                "speech_samples": speech.samples,
                "remote_audio": speech.remote,
                "renewal": self.renewal.report() if self.renewal is not None else None,
            },
        )
        return True
