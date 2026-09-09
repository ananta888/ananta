"""Two installed Workers executing a real Hub speaker floor; synthetic PCM only."""

import time

from sqlalchemy import select

from agent.models.meet_speaker_floor import SpeakerOwner
from agent.repositories.meet_speaker_floor import SqlMeetSpeakerFloor
from agent.repositories.meet_speaker_floor import turns as floor_turns
from agent.services.meet_dialog_speaker_floor import MeetDialogSpeakerFloor
from agent.services.meet_speaker_floor import MeetSpeakerFloor
from agent.services.meet_speaker_policy import MeetSpeakerPolicy
from tests.meet_dialog_interruption import synthetic_tone_result
from tests.meet_multi_worker_media import MultiWorkerMediaScenario, TwoReplyToneWorker


class FloorTone:
    def __init__(self, interrupt):
        if type(interrupt) is not bool:
            raise ValueError("test_floor_mode_invalid")
        self.durations = (20 if interrupt else 14, 4)
        self.calls = 0

    def execute(self, turn):
        if self.calls >= 2:
            raise ValueError("test_floor_reply_budget")
        seconds = self.durations[self.calls]
        self.calls += 1
        return synthetic_tone_result(turn, seconds)


class MultiWorkerSpeakerFloor:
    capabilities = MultiWorkerMediaScenario.capabilities
    start_options = MultiWorkerMediaScenario.start_options

    def __init__(self, interrupt):
        self.tone = FloorTone(interrupt)
        self.interrupt = interrupt
        self.media = MultiWorkerMediaScenario(worker=TwoReplyToneWorker(tone=self.tone))
        self.preemptions = []
        self.store = None

    @property
    def worker(self):
        return self.media.worker  # Preserve the common bounded diagnostic interface.

    def initial_options(self, index):
        return self.media.initial_options(index)

    def prepare_inputs(self, *args):
        return self.media.prepare_inputs(*args)

    def floor_diagnostic(self):
        if self.store is None:
            return []
        with self.store.engine.connect() as connection:
            rows = connection.execute(select(floor_turns).order_by(floor_turns.c.sequence).limit(4)).mappings().all()
        return [
            {"state": row["state"], "sequence": row["sequence"], "priority": row["binding"]["priority"]} for row in rows
        ]

    def service_options(self, binding, dispatches):
        states = SqlMeetSpeakerFloor(dispatches.engine)
        states.initialize()
        preempt = states.preempt

        def observe(turn, now):
            result = preempt(turn, now)
            if result:
                assert len(self.preemptions) < 2, "bounded preemption observation exceeded"
                self.preemptions.append(now)
            return result

        states.preempt = observe
        self.store = states
        rules = (
            [
                {
                    "tenant_id": "synthetic",
                    "project_id": "synthetic",
                    "organization_id": "meet-test-org",
                    "role_slot_id": "meet-test-slot-second",
                    "policy_id": "synthetic-chair",
                    "revision": 1,
                    "priority": 2,
                    "barge_in": True,
                }
            ]
            if self.interrupt
            else []
        )
        coordinator = MeetDialogSpeakerFloor(MeetSpeakerFloor(states), states, policy=MeetSpeakerPolicy(rules))
        return self.media.service_options(binding, dispatches) | {"speaker_floor": coordinator}

    def exercise(self, app, service, principal, started, command, record_property, wait_chat_ready):
        task_ids = self.media.prepare_inputs(app, service, principal, started, command, wait_chat_ready)
        owners = []
        with app.app_context():
            for task_id in task_ids:
                context = service.tasks.get_by_id(task_id).worker_execution_context["meet_dialog"]
                assert context["speaker_floor"] is True
                scope = service.authority.current(task_id, context["lease_id"], context["runtime_id"])
                owners.append(SpeakerOwner.from_scope(scope))
        assert command("floor-start") == {"observing": True}
        assert command("ask") == {"sent": True}
        assert command("media", phase="first-speech") == {"audio": "first", "consecutive": 3}
        self.media.worker.await_room_cooldown(task_ids[0])
        self.media.control(app, service, principal, task_ids[1], chat=True)
        wait_chat_ready(1)
        assert command("media", phase="first-speech") == {"audio": "first", "consecutive": 3}
        assert command("ask") == {"sent": True}
        result = command("floor-result")
        assert set(result) == {
            "failed",
            "samples",
            "overlap",
            "max_gap_ms",
            "quiet_ms",
            "active",
            "counts",
            "first_at_ms",
            "last_at_ms",
        }
        assert result["failed"] is False and result["overlap"] == 0
        assert 3 <= result["samples"] <= 3000 and result["max_gap_ms"] <= 250
        assert result["counts"][0] >= 3 and result["counts"][1] >= 3
        assert result["last_at_ms"][0] < result["first_at_ms"][1]
        first_span = result["last_at_ms"][0] - result["first_at_ms"][0]
        if self.interrupt:
            assert len(self.preemptions) == 1
            stop_ms = result["last_at_ms"][0] - self.preemptions[0]
            assert 0 <= stop_ms <= 4000 and first_span < 18000
            assert result["first_at_ms"][1] >= self.preemptions[0] + 3900
        else:
            assert self.preemptions == [] and 10000 <= first_span <= 17000
            stop_ms = None
        assert command("answers") == {"replies": 2}
        assert [call[0] for call in self.media.worker.calls] == task_ids
        until = time.monotonic() + 3
        while any(self.store.projection(owner, int(time.time() * 1000)) is not None for owner in owners):
            assert time.monotonic() < until, "bounded speaker completion receipt missing"
            time.sleep(0.05)
        record_property(
            "two_worker_speaker_floor",
            {
                "synthetic_policy": True,
                "synthetic_pcm": True,
                "actual_gpu": False,
                "production_release_evidence": False,
                "mode": "barge-in" if self.interrupt else "fifo",
                "sampled_overlap": result["overlap"],
                "samples": result["samples"],
                "max_gap_ms": result["max_gap_ms"],
                "active_observations": result["counts"],
                "first_span_ms": first_span,
                "preemption_stop_ms": stop_ms,
                "reply_tasks": self.tone.calls,
            },
        )

    def survivor(self, command):
        self.media.survivor(command)


def multi_worker_media(mode):
    if mode is True:
        return MultiWorkerMediaScenario()
    if mode in {"speaker-fifo", "speaker-barge-in", "room-reconnect-media"}:
        return MultiWorkerSpeakerFloor(mode == "speaker-barge-in")
    return None
