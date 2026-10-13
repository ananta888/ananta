"""Persistent persona dialog with answer-scoped, owner-authorized execution ports."""

import os

from worker.meet_media.companion_assigned_provider import AssignedCompanionProvider
from worker.meet_media.companion_assigned_tools import assigned_toolbox
from worker.meet_media.companion_dialog import CompanionDialog, persona_for
from worker.meet_media.llm import generate
from worker.meet_media.llm_backends import backend_name
from worker.meet_media.ollama_http import OllamaHttp
from worker.meet_media.openai_http import OpenAiHttp


class AnswerPorts:
    def __init__(self):
        self.current = None

    def call(self, port, arguments):
        if self.current is None:
            raise ValueError("meet_companion_answer_ports_unbound")
        return self.current.call(port, arguments)


class AssignedCompanionDialog:
    def __init__(self):
        self.ports = AnswerPorts()
        self.tools, self.receipts = assigned_toolbox(self.ports)
        backend = backend_name()
        provider = OpenAiHttp() if backend == "openai" else OllamaHttp()
        self.provider = AssignedCompanionProvider(provider, self.ports, backend=backend,
                                                  tool_results=self.receipts.snapshot)
        self.dialog = CompanionDialog(
            llm=lambda text, context, system: generate(text, context=context, system=system,
                tools=self.tools, transport=self.provider).text,
            tools=self.tools, system=persona_for(self.tools), codecompass_enabled=False,
            model_name=os.environ.get("MEET_LLM_MODEL", ""),
        )

    def answer(self, text, ports):
        if self.ports.current is not None:
            raise ValueError("meet_companion_answer_already_active")
        self.tools.reset()
        self.provider.last_completion = None
        self.ports.current = ports
        try:
            reply, _trace = self.dialog.answer(text)
            return reply
        finally:
            self.ports.current = None
