from __future__ import annotations

import os
import stat
from urllib.parse import urlsplit
from pathlib import Path
from types import SimpleNamespace

from client_surfaces.freecad.workbench.client import FreecadHubClient
from client_surfaces.freecad.workbench.commands import (
    preview_active_export_plan,
    preview_active_macro_plan,
    submit_active_document_goal,
)
from client_surfaces.freecad.workbench.execution import execute_approved_macro
from client_surfaces.freecad.workbench.settings import FreecadWorkbenchSettings
from scripts.run_freecad_install_smoke import evaluate_install_smoke

ROOT = Path(__file__).resolve().parents[2]


class _FlaskResponseAdapter:
    def __init__(self, response) -> None:
        self._response = response
        self.status = int(response.status_code)

    def read(self) -> bytes:
        return self._response.get_data()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FlaskUrlOpener:
    def __init__(self, client) -> None:
        self._client = client

    def open(self, req, timeout=None):  # noqa: ANN001
        split = urlsplit(req.full_url)
        path = split.path or "/"
        if split.query:
            path = f"{path}?{split.query}"
        response = self._client.open(
            path=path,
            method=req.get_method(),
            headers=dict(req.header_items()),
            data=req.data,
        )
        return _FlaskResponseAdapter(response)


def _runtime_modules() -> tuple[object, object]:
    constraint = SimpleNamespace(Name="C1", Type="Distance", Status="ok")
    body = SimpleNamespace(
        Label="Body",
        Name="Body",
        TypeId="Part::Feature",
        ViewObject=SimpleNamespace(Visibility=True),
        Shape=SimpleNamespace(Volume=12.5),
        Constraints=[constraint],
    )
    sketch = SimpleNamespace(
        Label="Sketch",
        Name="Sketch",
        TypeId="Sketcher::SketchObject",
        ViewObject=SimpleNamespace(Visibility=False),
        Shape=SimpleNamespace(Volume=0.0),
        Constraints=[],
    )
    document = SimpleNamespace(Name="Assembly", FileName="/tmp/demo.FCStd", UnitSystem="mm", Objects=[body, sketch])
    app_module = SimpleNamespace(ActiveDocument=document)
    gui_module = SimpleNamespace(Selection=SimpleNamespace(getSelection=lambda: [body]))
    return app_module, gui_module


def _fake_freecad_binary(tmp_path: Path) -> Path:
    script = tmp_path / "fake-freecadcmd"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"exec {os.environ.get('PYTHON_FOR_FREECAD_E2E', 'python3')} \"$1\"\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def test_freecad_runtime_golden_path_end_to_end(app, client, tmp_path: Path) -> None:
    settings = FreecadWorkbenchSettings(
        endpoint="http://freecad-e2e.local",
        token=str(app.config["AGENT_TOKEN"]),
        profile="freecad-e2e",
        transport_mode="http",
        allow_insecure_http=True,
    )
    transport_client = FreecadHubClient.with_http_transport(settings, opener=_FlaskUrlOpener(client))
    app_module, gui_module = _runtime_modules()

    goal_result = submit_active_document_goal(
        transport_client,
        goal="Inspect the active FreeCAD document for weak constraints",
        app_module=app_module,
        gui_module=gui_module,
    )
    export_result = preview_active_export_plan(
        transport_client,
        fmt="step",
        target_path="/tmp/out.step",
        app_module=app_module,
        gui_module=gui_module,
    )
    macro_plan_result = preview_active_macro_plan(
        transport_client,
        objective="reduce weight of selected body",
        app_module=app_module,
        gui_module=gui_module,
    )
    blocked_exec = execute_approved_macro(
        transport_client,
        script_hash="hash-1",
        session_id="s-freecad",
        correlation_id="corr-1",
        approval_id=None,
        macro_text="print('blocked')",
    )
    approved_exec = execute_approved_macro(
        transport_client,
        script_hash="hash-2",
        session_id="s-freecad",
        correlation_id="corr-2",
        approval_id="APR-1",
        macro_text="print('approved')",
    )

    assert goal_result["status"] == "accepted"
    assert goal_result["response"]["goal"]["source"] == "freecad_client_surface"
    assert export_result["status"] == "accepted"
    assert export_result["response"]["plan"]["selection_only"] is True
    assert macro_plan_result["status"] == "accepted"
    assert macro_plan_result["response"]["plan"]["mode"] == "dry_run"
    assert blocked_exec["status"] == "blocked"
    assert approved_exec["status"] == "accepted"

    os.environ["PYTHON_FOR_FREECAD_E2E"] = str(ROOT / ".venv" / "bin" / "python")
    try:
        fake_binary = _fake_freecad_binary(tmp_path)
        install_report = evaluate_install_smoke(root=ROOT, binary=str(fake_binary))
    finally:
        os.environ.pop("PYTHON_FOR_FREECAD_E2E", None)

    assert install_report["status"] == "passed"
    assert install_report["ok"] is True
    assert "workbench=Gui::PythonWorkbench" in str(install_report.get("stdout_tail") or "")
    assert "commands=AnantaCaptureContext,AnantaSubmitGoal,AnantaPreviewExportPlan,AnantaPreviewMacroPlan,AnantaExecuteMacro" in str(install_report.get("stdout_tail") or "")
