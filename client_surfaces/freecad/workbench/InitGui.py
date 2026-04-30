from __future__ import annotations

from client_surfaces.freecad.workbench import register, unregister
from client_surfaces.freecad.workbench.ananta_freecad_workbench import AnantaFreecadWorkbench

WORKBENCH = AnantaFreecadWorkbench()
REGISTER_PAYLOAD = register()
UNREGISTER_PAYLOAD = unregister()
