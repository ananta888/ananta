"""Execute a chat round under continuously current assigned Hub source controls."""

from worker.meet_media.companion_task_controls import CompanionSourceInterrupted


class AssignedCompanionExecution:
    """Model/TTS execution only; caller owns browser reconciliation and task lifecycle.

    A source interruption escapes ordinary answer-error handlers. A delivered
    chat reply remains delivered even when subsequent optional speech is paused.
    """

    def __init__(self, controls, execution, *, cooperate, publish_speech):
        self.controls, self.execution = controls, execution
        self.cooperate, self.publish_speech = cooperate, publish_speech

    def _guard(self, checkpoint):
        self.cooperate()
        self.controls.require_current(checkpoint)

    def answer(self, event, deliver):
        self.cooperate()
        chat = self.controls.checkpoint("chat")
        reply = self.execution.call("answer", event["text"], cooperate=lambda: self._guard(chat))
        self._guard(chat)
        if deliver(reply) is False:
            return
        if "speech" not in self.controls.state["controls"]:
            return
        try:
            speech = self.controls.checkpoint("speech")
            pcm = self.execution.call("speech", reply, cooperate=lambda: self._guard(speech))
            self._guard(speech)
            self.publish_speech(pcm, cooperate=lambda: self._guard(speech))
        except CompanionSourceInterrupted:
            # Speech is optional after chat delivery. Its paused operation is
            # retired, never replayed or converted into a second chat answer.
            return
