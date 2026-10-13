"""One assigned provider port; every inference consumes a separate Hub run."""

from copy import deepcopy
from typing import Protocol


class CompanionProviderPort(Protocol):
    @property
    def endpoint(self): ...

    def chat(self, payload): ...
    def models(self): ...


class AssignedCompanionProvider:
    def __init__(self, provider: CompanionProviderPort, ports, *, backend, tool_results):
        if backend not in {"openai", "ollama"}:
            raise ValueError("meet_companion_model_backend_invalid")
        self.provider, self.ports, self.backend = provider, ports, backend
        self.tool_results = tool_results
        self.last_completion = None

    @property
    def endpoint(self):
        return self.provider.endpoint

    def models(self):
        return self.provider.models()

    def chat(self, payload):
        self.last_completion = None
        request = deepcopy(payload)
        reservation = self.ports.call("model", {"action": "reserve", "payload": {
            "backend": self.backend, "model": request["model"], "request": request,
            "tool_results": deepcopy(self.tool_results()),
        }})
        result = self.provider.chat(deepcopy(request))
        self.last_completion = self.ports.call("model", {"action": "finish", "payload": {
            "run": reservation["run"], "input_source": reservation["input_source"], "result": deepcopy(result),
        }})
        return result
