"""One bounded Hub-assigned Companion session; no supervisor or task admission."""

import base64
import os
import sys
import time
from contextlib import ExitStack

from worker.meet_media.companion_browser_progress import CompanionBrowserProgress
from worker.meet_media.companion_chat_inbox import CompanionChatInbox
from worker.meet_media.companion_chat_inbox_processing import serve_inbox
from worker.meet_media.companion_chat_ledger import ChatLedger
from worker.meet_media.companion_chat_pump import CompanionChatPump, CompanionSessionEnded
from worker.meet_media.companion_media_process import CompanionMediaPreparationProcess
from worker.meet_media.companion_task_controls import (
    CompanionControlLost,
    CompanionSourceInterrupted,
    CompanionTaskControls,
)
from worker.meet_media.companion_task_environment import assigned_model_environment
from worker.meet_media.companion_task_execution import AssignedCompanionExecution
from worker.meet_media.companion_task_model_selection import assigned_execution
from worker.meet_media.companion_task_scratch import TaskExecutionScratch


def _run_until_deadline(runtime, deadline, *, clock=time.monotonic, sleep=time.sleep):
    """A planned duration ends normally; earlier authority loss remains terminal.

    This never executes after expiry or changes a Hub Task status. The Hub
    still fences the final callback and preserves its own cancellation state.
    """
    try:
        while clock() < deadline:
            runtime.tick()
            sleep(0.05)
    except (CompanionControlLost, CompanionSessionEnded):
        if clock() < deadline:
            raise


class AssignedCompanionSession:
    """Browser owner-thread adapter; all authority comes from the signed client."""

    def __init__(self, page, client, execution, media, controls, *, log, progress=None, observation=None):
        self.page, self.client, self.media, self.controls, self.log = page, client, media, controls, log
        self.inbox, self.ledger = CompanionChatInbox(), ChatLedger()
        self.avatar_generation = None
        self.avatar_opened = 0
        self.avatar_revision = None
        self.speech_revision = None
        self.progress = progress
        self.observation = observation
        self.sequence_active = False
        self.browser_progress = CompanionBrowserProgress()
        self.pump = CompanionChatPump(
            self.inbox, status=lambda: page.evaluate("() => window.anantaMachine.status()"),
            open_chat=lambda: page.evaluate("() => window.anantaMachine.chat.open()"),
            poll=lambda cursor: page.evaluate("(c) => window.anantaMachine.chat.poll(c)", cursor),
            redeliver=lambda ids: page.evaluate("(ids) => window.anantaMachine.chat.redeliver(ids)", ids),
            reply_pending=lambda identity: identity not in self.ledger.answered,
            renew=self.renew, stopped=lambda: False, log=log,
        )
        self.execution = AssignedCompanionExecution(
            controls, execution, cooperate=self.step, publish_speech=self.speech,
        )

    def renew(self):
        grant = self.client.read()
        from worker.meet_media.dialog_membership_loss import LOCAL_MEMBERSHIP

        local = self.page.evaluate(LOCAL_MEMBERSHIP)
        lease = local.get("lease") if isinstance(local, dict) else None
        expiry = lease.get("expiresAt") if isinstance(lease, dict) else None
        deadline = self.client.assignment["deadline"] * 1000
        if (not isinstance(local, dict) or local.get("joined") is not True
                or type(expiry) is not int or expiry > deadline):
            raise ValueError("meet_companion_renewal_scope_invalid")
        # Meet requires a strict extension. Once the browser lease already
        # reaches the original Task deadline, a fresh grant cannot extend it.
        # Current signed Hub reads continue; this grants no extra execution time.
        if expiry == deadline:
            return
        if self.sequence_active:
            return False
        speech = self.page.evaluate("() => window.anantaMachine.speech.status()")
        if not isinstance(speech, dict) or speech.get("state") not in {
            "starting", "open", "closed", "completed", "failed"
        }:
            raise ValueError("meet_companion_renewal_speech_state_invalid")
        if speech["state"] in {"starting", "open"}:
            # The native port is fenced to its current lease generation.
            # Keep that original, still-expiring lease until its bounded
            # utterance finishes; never replace authority under queued PCM.
            return False
        self.page.evaluate("(g) => window.anantaMachine.renew(g)", grant["grant"])

    def step(self):
        observed = self.controls.step()
        if observed is not None and self.progress is not None:
            self.progress.report(self.controls.fresh_until, self.client.deadline)
        if self.controls.state is None:
            return None
        if self.observation is not None:
            try:
                self.observation.poll()
            except Exception:
                raise CompanionControlLost("meet_companion_observation_lost") from None
        current = self.controls.state["controls"]
        avatar = current.get("avatar")
        speech = current.get("speech")
        if self.avatar_generation is not None and (not avatar["enabled"] or avatar != self.avatar_revision):
            self.page.evaluate("(g) => window.anantaMachine.avatar.close(g)", self.avatar_generation)
            self.avatar_generation = None
        if speech is not None and (not speech["enabled"] or self.speech_revision is not None
                                   and speech != self.speech_revision):
            self.page.evaluate("() => window.anantaMachine.speech.close()")
        self.speech_revision = speech
        return self.browser_progress.step(poll=self.pump.step, pulse=self._pulse_avatar)

    def _pulse_avatar(self):
        if self.avatar_generation is not None:
            self.page.evaluate("(g) => window.anantaMachine.avatar.pulse(g)", self.avatar_generation)

    def avatar(self):
        row = self.controls.state["controls"].get("avatar")
        if row is None or not row["enabled"]:
            return
        if self.avatar_generation is not None:
            if time.monotonic() - self.avatar_opened < 90:
                return
            self.page.evaluate("(g) => window.anantaMachine.avatar.close(g)", self.avatar_generation)
            self.avatar_generation = None
        checkpoint = self.controls.checkpoint("avatar")
        payload = self.media.idle("idle", cooperate=self.step)
        self.controls.require_current(checkpoint)
        source = "avatar:" + self.client.assignment["session_id"]
        receipt = self.page.evaluate(
            "(a) => window.anantaMachine.avatar.open(a[0], 'persona-video-v1', a[1])", [source, payload],
        )
        self.avatar_generation = receipt["generation"]
        self.avatar_opened = time.monotonic()
        self.avatar_revision = row

    def speech(self, pcm, *, cooperate):
        from worker.meet_media.companion_media import AvatarPorts
        from worker.meet_media.companion_task_speech import AssignedCompanionSpeech

        identity = self.client.assignment["session_id"]

        def open_avatar(payload):
            receipt = self.page.evaluate(
                "(a) => window.anantaMachine.avatar.open(a[0], 'persona-video-v1', a[1])",
                ["avatar:" + identity, payload])
            self.avatar_generation = receipt["generation"]
            self.avatar_revision = self.controls.state["controls"]["avatar"]
            self.avatar_opened = time.monotonic()
            return self.avatar_generation

        def close_avatar(generation):
            # Cooperative reconciliation may already have closed this generation.
            if self.avatar_generation == generation:
                self.page.evaluate("(g) => window.anantaMachine.avatar.close(g)", generation)
                self.avatar_generation = None

        def open_speech(total):
            return self.page.evaluate("(a) => window.anantaMachine.speech.open(a[0], a[1])",
                                      ["speech:" + identity, total])["generation"]

        def push_speech(generation, offset, chunk):
            self.page.evaluate("(a) => window.anantaMachine.speech.push(a[0], a[1], a[2])",
                               [generation, offset, base64.b64encode(chunk).decode()])

        ports = AvatarPorts(open_avatar, close_avatar, open_speech, push_speech,
                            speech_status=lambda: self.page.evaluate("() => window.anantaMachine.speech.status()"))
        sequence = None
        if os.environ.get("MEET_COMPANION_NATIVE_SEQUENCE") == "1":
            from worker.meet_media.companion_native_sequence import NativeCompanionSequence

            sequence = NativeCompanionSequence(self)
        publication = AssignedCompanionSpeech(self.controls, self.media, ports, cooperate=cooperate, sequence=sequence)
        try:
            _generation, report = publication.publish(pcm, current_generation=self.avatar_generation)
            self.log("speech_sync complete=%s retired=%s max_drift_us=%s" % (
                report.get("sync_complete", True), report.get("retired_segments", 0), report["max_drift_us"]))
        finally:
            # Global loss is cleaned up by the owned browser's ExitStack. Only
            # a still-current avatar may return to idle independently of speech.
            if not self.controls.closed:
                if self.avatar_generation is not None:
                    close_avatar(self.avatar_generation)
                self.avatar()

    def tick(self):
        state = self.step()
        if state is None:
            return
        self.ledger.observe(state.get("chat"), delivered_message_ids=self.inbox.pending_message_ids())
        try:
            self.avatar()
            if self.controls.state["controls"]["chat"]["enabled"]:
                serve_inbox(
                    self.inbox, self.ledger,
                    send=lambda identity, reply: self.page.evaluate(
                        "(a) => window.anantaMachine.chat.reply(a[0], a[1])", [identity, reply]),
                    ack=lambda cursor: self.page.evaluate("(c) => window.anantaMachine.chat.ack(c)", cursor),
                    answer=self.execution.answer, log=self.log,
                )
        except CompanionSourceInterrupted:
            self.log("source_interrupted event=operation_retired")


def run(client, *, log, progress=None, hub=None):
    """Execute only the provided assignment; cancellation never rejoins."""
    from playwright.sync_api import sync_playwright

    from worker.meet_media.browser_network import restrict_meet_browser_network
    from worker.meet_media.dialog_session_operations import DialogSessionOperations

    if not {"chat.read", "chat.send"} <= set(client.assignment["capabilities"]):
        raise ValueError("meet_companion_chat_assignment_required")
    with sync_playwright() as playwright, ExitStack() as cleanup:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                                              args=["--autoplay-policy=no-user-gesture-required"])
        cleanup.callback(browser.close)
        context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
        context.add_init_script("""for (const name of ['getUserMedia', 'getDisplayMedia']) {
          navigator.mediaDevices[name] = () => Promise.reject(new Error('human_capture_forbidden'));
        }""")
        origin = client.assignment["meeting"]["origin"]
        restrict_meet_browser_network(context, origin)
        page = context.new_page()
        page.set_default_timeout(15000)
        url = origin + "/machine"
        page.goto(url, wait_until="domcontentloaded")
        session = DialogSessionOperations(page, url=url, deadline=client.deadline)
        cleanup.callback(session.close)
        session.ready(client.assignment["capabilities"])
        grant = client.read()
        session.join(grant["room_id"], grant["grant"])
        controls = CompanionTaskControls(client)
        cleanup.callback(controls.close)
        execution = assigned_execution(client, grant)
        cleanup.callback(execution.close)
        media_scratch = TaskExecutionScratch(parent=os.environ.get("ANANTA_MEET_TASK_SCRATCH_DIR", "/tmp"))
        cleanup.callback(media_scratch.close)
        media = CompanionMediaPreparationProcess(
            environment=assigned_model_environment(client.assignment) | media_scratch.environment)
        cleanup.callback(media.close)
        observation = None
        if hub is not None:
            from worker.meet_media.dialog_control_exchange import DialogControlExchange
            from worker.meet_media.dialog_membership_loss import LOCAL_MEMBERSHIP

            meet_session = page.evaluate(LOCAL_MEMBERSHIP)["lease"]["sessionId"]
            observation = DialogControlExchange(None, None,
                read=lambda: hub.call("observe", meet_session_id=meet_session))
            cleanup.callback(observation.close)
        runtime = AssignedCompanionSession(page, client, execution, media, controls, log=log,
                                           progress=progress, observation=observation)
        _run_until_deadline(runtime, client.deadline)


def main():
    """Inherited closed assignment and existing Hub terminal callback contract."""
    from ananta_contracts.meet_dialog import MAX_DIALOG_BYTES, parse, validate_assignment
    from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
    from worker.meet_media.dialog_client import HubDialogClient
    from worker.meet_media.dialog_progress_channel import inherited_progress

    with sys.stdin.buffer as source:
        assignment = validate_assignment(parse(source.read(MAX_DIALOG_BYTES + 1)), time.time())
    hub = HubDialogClient(assignment)
    endpoint = (os.environ.get("MEET_HUB_COMPANION_TASK_URL")
                or hub.url.removesuffix("/dialog") + "/companion/task-grant")
    client = AssignedCompanionGrantClient(assignment, endpoint, hub.key)
    status = "failed"
    try:
        with inherited_progress() as progress:
            run(client, log=lambda _event: None, progress=progress, hub=hub)
        status = "completed"
    finally:
        try:
            hub.call("finish", status=status)
        except Exception:
            pass  # Closed Hub lease rechecks may reject a cancelled/stale finish.


if __name__ == "__main__":
    try:
        main()
    except (Exception, CompanionControlLost):
        sys.exit(1)  # No private grant, model content or upstream error is printed.
