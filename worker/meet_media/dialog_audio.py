"""Execute a delegated utterance while the browser owner keeps checking authority."""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

from ananta_contracts.meet_audio_profile import optional_audio_profile
from ananta_contracts.meet_audio_segment import require_segment_finished, require_segment_probe
from ananta_contracts.meet_dialog_audio import audio_job_current, validate_audio_job
from worker.meet_media.audio_batch import AudioBatchCursor
from worker.meet_media.audio_segment import segment_boundary


class AudioLease:
    def __init__(self, binding, job):
        self.binding, self.job = binding, job
        self.lock = threading.Lock()
        self.closed = False
        self.checked_at = time.monotonic()

    def refresh(self, receipt, active_job):
        with self.lock:
            if active_job != self.job or not audio_job_current(self.job, receipt, time.time()):
                self.closed = True
            else:
                self.checked_at = time.monotonic()

    def require(self, binding):
        with self.lock:
            if (
                self.closed
                or binding != self.binding
                or time.time() >= self.job["deadline"]
                or time.monotonic() - self.checked_at > 6
            ):
                raise ValueError("meet_audio_lease_revoked")

    def close(self):
        with self.lock:
            self.closed = True


def bind_subscription(subscription, assignment, job):
    """Map distinct Hub child-task and browser-session leases, never relabel epochs."""
    profile = optional_audio_profile(job)
    expected = {"schema", "binding", "subscriptionId", "format", "sampleRate", "channels", "chunkSamples", "maxSeconds"}
    if (
        not isinstance(subscription, dict)
        or set(subscription) != expected
        or subscription["schema"] != "ananta.meet-audio-subscription.draft1"
        or subscription["format"] != "pcm_s16le"
        or subscription["sampleRate"] != 16000
        or subscription["channels"] != 1
        or subscription["chunkSamples"] != 1600
        or subscription["maxSeconds"] != profile.segment_seconds
        or any(type(subscription[k]) is not int for k in ("sampleRate", "channels", "chunkSamples", "maxSeconds"))
        or not isinstance(subscription["subscriptionId"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", subscription["subscriptionId"])
    ):
        raise ValueError("meet_audio_subscription_invalid")
    binding = subscription["binding"]
    expected = {k: assignment[k] for k in ("tenant_id", "project_id", "task_id", "runtime_id", "session_id")}
    expected |= {
        "lease_id": job["meet_session_id"],
        "generation": job["generation"],
        "room_id": assignment["meeting"]["room_id"],
        "membership_epoch": job["membership_epoch"],
        "peer_id": job["peer_id"],
        "own_peer_id": job["own_peer_id"],
        "publication_id": job["publication_id"],
        "receive_revision": job["receive_revision"],
        "source": "screen_audio" if job["source"] == "screen-audio" else "microphone",
    }
    if (
        not isinstance(binding, dict)
        or set(binding) != set(expected) | {"deadline_ms"}
        or any(type(binding[k]) is not type(v) or binding[k] != v for k, v in expected.items())
        or type(binding["deadline_ms"]) is not int
        or binding["deadline_ms"] < job["deadline"] * 1000
    ):
        raise ValueError("meet_audio_subscription_mismatch")
    from worker.meet_media.audio_receive import ReceiveBinding

    return ReceiveBinding(
        **{k: v for k, v in expected.items() if k != "receive_revision" and k not in {"task_id", "lease_id"}},
        task_id=job["task_id"],
        lease_id=job["lease_id"],
        publication_epoch=job["publication_epoch"],
    )


class DialogAudioPump:
    def __init__(self, page, hub, assignment, job):
        from worker.meet_media.asr_pipeline import MeetAsrPipeline
        from worker.meet_media.audio_receive import MeetAudioReceiver

        self.page, self.hub, self.assignment = page, hub, assignment
        self.job = validate_audio_job(job, time.time())
        if job.get("audio_profile") != assignment.get("audio_profile"):
            raise ValueError("meet_audio_profile_mismatch")
        profile = optional_audio_profile(job)
        self.cursor = AudioBatchCursor(profile)
        self.segment = segment_boundary(profile)
        self.early_segments = profile.segmentation == "energy-v1"
        with ExitStack() as setup:
            setup.callback(self._close_browser)
            if self.early_segments:
                require_segment_probe(page.evaluate("() => window.anantaMachine?.audio?.segmentProbe?.() ?? null"))
            self.subscription = page.evaluate(
                "([id, seconds]) => window.anantaMachine.audio.open(id, seconds)",
                [job["publication_id"], profile.segment_seconds],
            )
            self.binding = bind_subscription(self.subscription, assignment, job)
            self.lease = AudioLease(self.binding, job)
            setup.callback(self.lease.close)
            deadline = time.monotonic() + min(30, job["deadline"] - time.time())
            pipeline = MeetAsrPipeline(
                self.binding,
                self.lease,
                deadline_monotonic=deadline,
                **({"audio_profile": profile.projection()} if "audio_profile" in job else {}),
            )
            setup.callback(pipeline.cancel)
            self.receiver = MeetAudioReceiver(
                self.binding,
                self.lease,
                pipeline,
                deadline_monotonic=deadline,
                language=profile.language,
                max_audio_seconds=profile.segment_seconds,
            )
            setup.callback(self.receiver.close)
            self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meet-local-asr")
            setup.pop_all()
        self.pending = None
        self.stage = "receive"
        self.closed = False

    def refresh(self, receipt, job):
        self.lease.refresh(receipt, job)

    def tick(self):
        if self.closed:
            return
        self.lease.require(self.binding)
        if not self.page.evaluate("window.anantaMachine.audio.status().open"):
            raise ValueError("meet_audio_browser_revoked")
        if self.stage == "receive":
            batch = self.page.evaluate("window.anantaMachine.audio.poll()")
            for chunk in self.cursor.validate(batch):
                self.receiver.push(self.binding, start_sample=chunk.start_sample, pcm=chunk.pcm)
                self.page.evaluate("sequence => window.anantaMachine.audio.ack(sequence)", chunk.sequence)
                self.cursor.acknowledge(chunk.sequence)
                if self.segment.push(chunk.pcm) and self.early_segments:
                    end_sample = chunk.sequence * 1600
                    result = self.page.evaluate(
                        "([id, end]) => window.anantaMachine.audio.finish(id, end)",
                        [self.subscription["subscriptionId"], end_sample],
                    )
                    require_segment_finished(result, self.subscription["subscriptionId"], end_sample)
                    self.stage = "asr"
                    self.pending = self.pool.submit(self.receiver.finish)
                    return
            if self.cursor.complete(batch):
                self.stage = "asr"
                self.pending = self.pool.submit(self.receiver.finish)
        elif self.pending.done():
            result = self.pending.result()
            if self.stage == "asr":
                self.stage = "reply"
                self.pending = self.pool.submit(
                    self.hub.call,
                    "transcript",
                    meet_session_id=self.job["meet_session_id"],
                    audio_task_id=self.job["task_id"],
                    audio_lease_id=self.job["lease_id"],
                    end_sample=result.end_sample,
                    language=result.language,
                    text=result.text,
                )
            else:
                if result["reply"] is not None:
                    if self.assignment["audio_mode"] != "dialog":
                        raise ValueError("meet_audio_reply_policy_denied")
                    reply = result["reply"]
                    if (
                        not isinstance(reply, dict)
                        or set(reply) != {"text"}
                        or not isinstance(reply["text"], str)
                        or not 0 < len(reply["text"]) <= 450
                    ):
                        raise ValueError("meet_audio_reply_invalid")
                    self.page.evaluate(
                        "([id, text]) => window.anantaMachine.audio.reply(id, text)",
                        [self.subscription["subscriptionId"], reply["text"]],
                    )
                self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.lease.close()
        if self.pending is not None:
            self.pending.cancel()
            self.pending = None
        # Cancel outside the browser loop; ASR cancellation checks kill its child.
        self.receiver.pipeline.cancel()
        self.pool.submit(self.receiver.close)
        self.pool.shutdown(wait=False, cancel_futures=False)
        self._close_browser()

    def _close_browser(self):
        try:
            self.page.evaluate("window.anantaMachine.audio.close()")
        except Exception:
            pass  # The browser may already be gone; native cleanup still runs.
