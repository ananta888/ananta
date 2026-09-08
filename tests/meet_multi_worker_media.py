"""Real publication scenario; explicitly synthetic profiles and inference only."""

import copy
import threading
import time

from agent.services.meet_chat_policy import ChatReplyPolicy
from agent.services.meet_dialog_replies import MeetDialogReplies
from agent.services.meet_turn_service import HubMediaTasks
from ananta_contracts.meet_speech import speech_profile
from tests.meet_dialog_image_avatar_scenario import SyntheticImageProfiles
from tests.meet_dialog_interruption import SyntheticToneWorker


class IndependentlyRevocableImages:
    """Two deterministic test-only pins, each with its own revocation fence."""

    def __init__(self):
        self._base = SyntheticImageProfiles()
        self._revoked = set()
        for index, (_image, pin) in enumerate(self._base.catalog.values()):
            pin.update(organization_id="meet-test-org", owner_kind="agent", owner_id=f"synthetic-agent-{index}")

    def pin(self, color):
        if color not in {"red", "blue"}:
            raise ValueError("test_multi_image_invalid")
        return copy.deepcopy(self._base.catalog[color][1])

    def prepare(self, principal, project, selection, purpose):
        with self._base.lock:
            image, pin = self._base.prepare(principal, project, selection, purpose)
            if pin["selection_digest"] in self._revoked:
                raise PermissionError("test_multi_image_revoked")
            return image, pin

    def select(self, principal, project, selection, purpose):
        image, pin = self.prepare(principal, project, selection, purpose)
        return image["reference"], pin

    def require_current(self, principal, project, selection, reference, purpose):
        image, _ = self.prepare(principal, project, selection, purpose)
        if image["reference"] != reference:
            raise PermissionError("test_multi_image_reference_changed")

    def revoke(self, color):
        with self._base.lock:
            self._revoked.add(self.pin(color)["selection_digest"])


class TwoReplyToneWorker:
    """Only records bounded child execution timing/bindings, never input or PCM."""

    def __init__(self):
        self._tone = SyntheticToneWorker(seconds=20)
        self._condition = threading.Condition()
        self.calls = []

    def execute(self, turn):
        with self._condition:
            if len(self.calls) >= 2:
                raise ValueError("test_multi_reply_budget")
            self.calls.append((turn["binding_task_id"], time.monotonic()))
            self._condition.notify_all()
        return self._tone.execute(turn)

    def await_room_cooldown(self, first_task):
        with self._condition:
            assert len(self.calls) == 1 and self.calls[0][0] == first_task, "first reply ownership mismatch"
            remaining = self.calls[0][1] + ChatReplyPolicy().cooldown_ms / 1000 + 0.1 - time.monotonic()
        assert remaining <= 11, "unexpected room cooldown policy"
        if remaining > 0:
            threading.Event().wait(remaining)


class MultiWorkerMediaScenario:
    capabilities = ["avatar.publish", "chat.read", "chat.send", "screen.publish", "speech.publish"]
    start_options = {"avatar_images": True, "chat_mode": "mention", "duration_seconds": 180}

    def __init__(self):
        self.images = IndependentlyRevocableImages()
        self.worker = TwoReplyToneWorker()

    def service_options(self, binding, dispatches):
        return {
            "avatar_profiles": self.images,
            "replies": MeetDialogReplies(
                binding, self.worker, HubMediaTasks(), dispatches, speech_profile=speech_profile(max_seconds=20)
            ),
        }

    @staticmethod
    def control(app, service, principal, task_id, **changes):
        with app.app_context():
            current = service.inspect(principal, "synthetic", task_id)["controls"]
            return service.control(
                principal,
                "synthetic",
                task_id,
                {
                    **{name: value["enabled"] for name, value in current.items() if name != "revision"},
                    "expected_revision": current["revision"],
                    **changes,
                },
            )

    def exercise(self, app, service, principal, started, command, record_property, wait_chat_ready):
        task_ids = [row["task_id"] for row in started]
        for index, color in enumerate(("red", "blue")):
            with app.app_context():
                current = service.inspect(principal, "synthetic", task_ids[index])["controls"]
                selected = service.select_avatar(
                    principal,
                    "synthetic",
                    task_ids[index],
                    {
                        "expected_revision": current["revision"],
                        "profile": self.images.pin(color),
                    },
                )
                assert not selected["controls"]["avatar"]["enabled"]
            self.control(app, service, principal, task_ids[index], avatar=True, chat=index == 0)
        assert command("media", phase="avatars") == {"personas": ["red", "blue"], "screens": [True, True]}
        for index in range(2):
            assert command("consent", publisher=index, enabled=True) == {"consent": index, "enabled": True}
        wait_chat_ready(0)
        assert command("ask") == {"sent": True}
        assert command("media", phase="first-speech") == {"audio": "first", "consecutive": 3}
        self.worker.await_room_cooldown(task_ids[0])
        self.control(app, service, principal, task_ids[1], chat=True)
        wait_chat_ready(1)
        assert command("ask") == {"sent": True}
        assert command("media", phase="both-speech") == {"audio": "both", "consecutive": 3}
        assert command("answers") == {"replies": 2}
        assert [call[0] for call in self.worker.calls] == task_ids, "exact two reply ownership bindings required"
        self.images.revoke("red")
        assert command("media", phase="first-revoked") == {"personas": [None, "blue"], "screens": [True, True]}
        assert command("screens") == {"moving": [True, True], "departedAbsent": False}
        record_property(
            "two_packaged_worker_media",
            {
                "synthetic_policy": True,
                "synthetic_inference": True,
                "actual_gpu": False,
                "single_host": True,
                "production_release_evidence": False,
                "distinct_personas": True,
                "simultaneous_audio_observations": 3,
                "independent_image_revocation": True,
                "reply_tasks": len(self.worker.calls),
            },
        )

    @staticmethod
    def survivor(command):
        assert command("media", phase="survivor") == {"personas": [None, "blue"], "screens": [False, True]}
