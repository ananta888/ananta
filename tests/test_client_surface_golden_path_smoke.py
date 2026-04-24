from __future__ import annotations

from scripts.smoke_client_golden_paths import run_golden_paths_once


def test_client_surface_golden_path_smoke_reports_success() -> None:
    ok, payload = run_golden_paths_once()

    assert ok is True
    assert payload["schema"] == "client_surface_golden_path_smoke_v1"
    assert payload["ok"] is True
    paths = {entry["path"]: entry for entry in payload["results"]}
    assert paths["tui"]["ok"] is True
    assert paths["nvim"]["ok"] is True
    assert paths["eclipse"]["ok"] is True
