"""Bounded line assembly and redacted events, independent of process control."""

from collections.abc import Sequence

from agent.cli_backends.coding_agent_contract import CodingAgentEvent, EventSink


class ProcessOutput:
    def __init__(self, limit: int, sink: EventSink | None, secrets: Sequence[str]):
        self.limit, self.sink = limit, sink
        self.secrets = tuple(value for value in secrets if len(value) >= 4)
        self.received = self.collected = self.sequence = 0
        self.exceeded = False
        self.pending: dict[str, list[str]] = {"stdout": [], "stderr": []}
        self.output: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def feed(self, stream: str, chunk: str | None) -> bool:
        if self.exceeded:
            return False
        if chunk is None:
            return self._emit(stream)
        self.received += len(chunk)
        if self.received > self.limit:
            self.exceeded = True
            return False
        # LF only: Unicode separators inside JSON strings are not records.
        for index, part in enumerate(chunk.split("\n")):
            if index:
                self.pending[stream].append("\n")
                if not self._emit(stream):
                    return False
            self.pending[stream].append(part)
        return True

    def _emit(self, stream: str) -> bool:
        value = "".join(self.pending[stream])
        self.pending[stream].clear()
        for secret in self.secrets:
            value = value.replace(secret, "<redacted>")
        if self.collected + len(value) > self.limit:
            self.exceeded = True
            return False
        if not value:
            return True
        self.collected += len(value)
        self.output[stream].append(value)
        self.sequence += 1
        if self.sink is not None:
            try:
                self.sink(CodingAgentEvent(self.sequence, stream, value.rstrip("\n")))
            except Exception:
                pass  # Preserve the existing observational event-sink contract.
        return True

    def text(self, stream: str) -> str:
        return "".join(self.output[stream])
