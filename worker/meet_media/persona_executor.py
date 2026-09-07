"""Compatible single-flight image executor using the shared fenced boundary."""

from ananta_contracts.persona_inspection_wire import IMAGE_WIRE
from worker.meet_media.persona_image_inspector import PersonaImageInspector
from worker.meet_media.persona_inspection_executor import PersonaInspectionExecutor


class PersonaImageExecutor:
    def __init__(self, replay_path, *, guard_factory):
        self._executor = PersonaInspectionExecutor(
            replay_path, guard_factory=guard_factory, wire=IMAGE_WIRE, inspector=PersonaImageInspector
        )
        self.replay_path, self.guard_factory = replay_path, guard_factory
        self.lock = self._executor.lock

    def execute(self, request):
        return self._executor.execute(request)
