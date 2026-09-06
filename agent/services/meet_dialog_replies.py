"""Shared dialog reply composition; all generation retains Hub media budgets."""

import time

from agent.services.meet_chat_reply_service import MeetChatReplyService
from ananta_contracts.meet_speech import validate_speech_profile


class MeetDialogReplies:
    def __init__(self, binding, worker, tasks, dispatches, *, clock=time.time, capacity=None, speech_profile=None):
        self.binding, self.worker, self.tasks, self.dispatches = binding, worker, tasks, dispatches
        self.clock, self.capacity = clock, capacity
        self.speech_profile = validate_speech_profile(speech_profile) if speech_profile is not None else None

    def execute(self, authority, principal, admission):
        return MeetChatReplyService(
            authority,
            self.dispatches,
            self.binding,
            self.worker,
            self.tasks,
            clock=self.clock,
            capacity=self.capacity,
            speech_profile=self.speech_profile,
        ).execute(principal, admission)
