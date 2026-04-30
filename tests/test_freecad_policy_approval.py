from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_freecad_policy_pack_is_default_deny_with_macro_execute_blocked() -> None:
    payload = json.loads((ROOT / "policies" / "freecad_policy.v1.json").read_text(encoding="utf-8"))
    assert payload["default"] == "deny"
    rules = {item["capability"]: item for item in payload["rules"]}
    assert rules["freecad.document.read"]["decision"] == "allow"
    assert rules["freecad.macro.execute"]["decision"] in {"deny", "confirm_required"}
