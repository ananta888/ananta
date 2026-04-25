from __future__ import annotations

import json
from pathlib import Path

from client_surfaces.common.types import ClientResponse
from client_surfaces.nvim_runtime import ananta_bridge

ROOT = Path(__file__).resolve().parents[1]


class _DummyClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def submit_goal(self, *, goal_text, context_payload):  # noqa: ANN001
        self.calls.append("submit_goal")
        return ClientResponse(True, 200, "healthy", {"goal_text": goal_text, "context": context_payload}, None, False)

    def analyze_context(self, *, context_payload):  # noqa: ANN001
        self.calls.append("analyze_context")
        return ClientResponse(True, 200, "healthy", {"context": context_payload}, None, False)

    def review_context(self, *, context_payload):  # noqa: ANN001
        self.calls.append("review_context")
        return ClientResponse(True, 200, "healthy", {"context": context_payload}, None, False)

    def patch_plan(self, *, context_payload):  # noqa: ANN001
        self.calls.append("patch_plan")
        return ClientResponse(True, 200, "healthy", {"context": context_payload}, None, False)

    def create_project_new(self, *, goal_text, context_payload):  # noqa: ANN001
        self.calls.append("create_project_new")
        return ClientResponse(True, 200, "healthy", {"goal_text": goal_text, "context": context_payload}, None, False)

    def create_project_evolve(self, *, goal_text, context_payload):  # noqa: ANN001
        self.calls.append("create_project_evolve")
        return ClientResponse(True, 200, "healthy", {"goal_text": goal_text, "context": context_payload}, None, False)


def test_nvim_bridge_dispatch_maps_commands_to_backend_client_methods(monkeypatch) -> None:
    monkeypatch.delenv("ANANTA_NVIM_FIXTURE", raising=False)
    client = _DummyClient()
    context_payload = {"schema": "client_bounded_context_payload_v1", "selection_text": "print('x')"}

    for command in ("goal_submit", "analyze", "review", "patch_plan", "project_new", "project_evolve"):
        response = ananta_bridge._dispatch_command(  # noqa: SLF001
            command=command,
            goal_text="Goal",
            context_payload=context_payload,
            client=client,  # type: ignore[arg-type]
        )
        assert response.ok is True

    assert client.calls == [
        "submit_goal",
        "analyze_context",
        "review_context",
        "patch_plan",
        "create_project_new",
        "create_project_evolve",
    ]


def test_nvim_bridge_main_emits_explicit_degraded_state(monkeypatch, capsys) -> None:
    def fake_dispatch(**kwargs):  # noqa: ANN001
        return ClientResponse(
            ok=False,
            status_code=403,
            state="policy_denied",
            data={"error": "denied"},
            error="request_failed:policy_denied",
            retriable=False,
        )

    monkeypatch.setattr(ananta_bridge, "_dispatch_command", fake_dispatch)
    rc = ananta_bridge.main(
        [
            "--command",
            "analyze",
            "--base-url",
            "http://localhost:8080",
            "--file-path",
            "/workspace/src/main.py",
            "--project-root",
            "/workspace",
            "--selection-text",
            "print('x')",
        ]
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 2
    assert payload["response"]["ok"] is False
    assert payload["response"]["state"] == "policy_denied"


def test_nvim_bridge_context_payload_is_bounded_and_tracks_provenance(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ANANTA_NVIM_FIXTURE", "1")
    rc = ananta_bridge.main(
        [
            "--command",
            "review",
            "--base-url",
            "http://localhost:8080",
            "--file-path",
            "/workspace/src/main.py",
            "--project-root",
            "/workspace",
            "--selection-text",
            "A" * 4000,
            "--max-selection-chars",
            "1200",
        ]
    )
    payload = json.loads(capsys.readouterr().out.strip())
    context = payload["context"]

    assert rc == 0
    assert context["file_path"] == "/workspace/src/main.py"
    assert context["project_root"] == "/workspace"
    assert context["selection_clipped"] is True
    assert len(context["selection_text"]) == 1200
    assert context["provenance"]["has_file_path"] is True
    assert context["provenance"]["has_project_root"] is True
    assert context["provenance"]["has_selection"] is True


def test_nvim_runtime_exposes_context_inspect_and_confirmation_path() -> None:
    plugin_content = (ROOT / "client_surfaces" / "nvim_runtime" / "plugin" / "ananta.vim").read_text(encoding="utf-8")
    init_content = (
        ROOT / "client_surfaces" / "nvim_runtime" / "lua" / "ananta" / "init.lua"
    ).read_text(encoding="utf-8")

    assert "AnantaContextInspect" in plugin_content
    assert "maybe_confirm_context" in init_content
    assert "Send bounded context to Ananta?" in init_content
    assert "show_context_preview" in init_content


def test_nvim_bridge_commands_do_not_mutate_files_silently(monkeypatch, tmp_path, capsys) -> None:
    source_file = tmp_path / "sample.py"
    original = "print('before')\n"
    source_file.write_text(original, encoding="utf-8")

    monkeypatch.setenv("ANANTA_NVIM_FIXTURE", "1")
    rc = ananta_bridge.main(
        [
            "--command",
            "goal_submit",
            "--base-url",
            "http://localhost:8080",
            "--file-path",
            str(source_file),
            "--project-root",
            str(tmp_path),
            "--selection-text",
            original,
        ]
    )
    payload = json.loads(capsys.readouterr().out.strip())

    assert rc == 0
    assert payload["response"]["ok"] is True
    assert source_file.read_text(encoding="utf-8") == original
