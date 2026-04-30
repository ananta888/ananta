from __future__ import annotations

from client_surfaces.freecad.workbench.ananta_freecad_workbench import (
    AnantaFreecadWorkbench,
    build_registration_payload,
)

WORKBENCH_NAME = "AnantaFreeCAD"
workbench_info = {
    "name": WORKBENCH_NAME,
    "entrypoint": "client_surfaces.freecad.workbench.InitGui",
    "description": "Thin FreeCAD workbench surface for bounded context capture and hub-routed actions.",
}


def register() -> dict[str, object]:
    return build_registration_payload()


def unregister() -> dict[str, str]:
    return {"status": "unregistered", "workbench": WORKBENCH_NAME}


__all__ = [
    "AnantaFreecadWorkbench",
    "WORKBENCH_NAME",
    "register",
    "unregister",
    "workbench_info",
]
