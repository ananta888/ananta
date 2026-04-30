from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FreecadCommandSpec:
    command_id: str
    title: str
    capability_id: str
    mutating: bool = False


DEFAULT_COMMANDS: tuple[FreecadCommandSpec, ...] = (
    FreecadCommandSpec("AnantaCaptureContext", "Capture Context", "freecad.document.read"),
    FreecadCommandSpec("AnantaSubmitGoal", "Submit Goal", "freecad.model.inspect"),
    FreecadCommandSpec("AnantaPreviewExportPlan", "Preview Export Plan", "freecad.export.plan"),
    FreecadCommandSpec("AnantaPreviewMacroPlan", "Preview Macro Plan", "freecad.macro.plan"),
    FreecadCommandSpec("AnantaExecuteMacro", "Execute Approved Macro", "freecad.macro.execute", True),
)

WORKBENCH_ENTRYPOINT = "Gui::PythonWorkbench"


class AnantaFreecadWorkbench:
    MenuText = "Ananta"
    ToolTip = "Bounded FreeCAD runtime surface routed through the Ananta hub."
    Icon = ""

    def __init__(self) -> None:
        self._commands = DEFAULT_COMMANDS
        self.activated = False

    def GetClassName(self) -> str:
        return WORKBENCH_ENTRYPOINT

    def Initialize(self) -> list[str]:
        return [item.command_id for item in self._commands]

    def Activated(self) -> None:
        self.activated = True

    def Deactivated(self) -> None:
        self.activated = False

    def list_commands(self) -> list[dict[str, object]]:
        return [asdict(item) for item in self._commands]


def build_registration_payload() -> dict[str, object]:
    workbench = AnantaFreecadWorkbench()
    return {
        "status": "registered",
        "workbench": "AnantaFreeCAD",
        "entrypoint": workbench.GetClassName(),
        "commands": workbench.list_commands(),
    }
