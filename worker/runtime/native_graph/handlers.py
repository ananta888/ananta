"""In-process operation selection for a node already assigned by the Hub."""

from collections.abc import Mapping

from worker.runtime.native_graph.contracts import NativeNodeCommand, NativeNodeResult
from worker.runtime.native_graph.ports import NativeNodeHandlerPort


class NativeTaskKindHandlers:
    def __init__(self, *, default: NativeNodeHandlerPort, handlers: Mapping[str, NativeNodeHandlerPort]) -> None:
        self._default, self._handlers = default, dict(handlers)

    def execute(self, command: NativeNodeCommand, *, hub_task_id: str) -> NativeNodeResult:
        handler = self._handlers.get(command.node.task_kind, self._default)
        return handler.execute(command, hub_task_id=hub_task_id)


class UnavailableNativeNodeHandler:
    def __init__(self, reason_code: str) -> None:
        self._reason = reason_code

    def execute(self, command: NativeNodeCommand, *, hub_task_id: str) -> NativeNodeResult:
        raise ValueError(self._reason)
