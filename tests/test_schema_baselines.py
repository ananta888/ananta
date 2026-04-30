import json
import os
import pytest
from pathlib import Path

BASELINES_DIR = Path(__file__).parent / "baselines"

def get_structure(data):
    """Rekursive Extraktion der Struktur (Keys und Typen) eines Dictionaries/Liste."""
    if isinstance(data, dict):
        return {k: get_structure(v) for k, v in data.items()}
    elif isinstance(data, list):
        if not data:
            return []
        # Wir prüfen nur die Struktur des ersten Elements in Listen, um es übersichtlich zu halten
        return [get_structure(data[0])]
    else:
        return type(data).__name__


def _is_structure_compatible(actual, expected):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, expected_value in expected.items():
            if key not in actual:
                return False
            if not _is_structure_compatible(actual[key], expected_value):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        if not expected:
            return True
        if not actual:
            return True
        expected_item = expected[0]
        return any(_is_structure_compatible(item, expected_item) for item in actual)
    return actual == expected

def assert_baseline(name, data):
    """Prüft, ob die Daten mit der Baseline des gegebenen Namens übereinstimmen."""
    os.makedirs(BASELINES_DIR, exist_ok=True)
    baseline_path = BASELINES_DIR / f"{name}.json"

    structure = get_structure(data)

    if os.environ.get("GENERATE_BASELINES") == "1":
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(structure, f, indent=2)
        print(f"\n[Baseline] Generated {baseline_path}")
        return

    if not baseline_path.exists():
        pytest.fail(f"Baseline for {name} missing. Run with GENERATE_BASELINES=1 to create it.")

    with open(baseline_path, "r", encoding="utf-8") as f:
        expected_structure = json.load(f)

    assert _is_structure_compatible(
        structure, expected_structure
    ), f"Structure mismatch for {name}. If this change is intentional, run with GENERATE_BASELINES=1 to update."

@pytest.mark.parametrize("endpoint, name", [
    ("/assistant/read-model", "assistant_read_model"),
    ("/dashboard/read-model", "dashboard_read_model"),
    ("/governance/policy", "governance_policy"),
    ("/tasks/orchestration/read-model", "orchestration_read_model"),
    ("/stats", "system_stats"),
])
def test_read_model_baselines(client, admin_auth_header, endpoint, name):
    """
    Vergleicht die Struktur von zentralen Read-Models mit gespeicherten Baselines.
    Dies stellt sicher, dass API-Verträge stabil bleiben (CNT-031).
    """
    response = client.get(endpoint, headers=admin_auth_header)
    assert response.status_code == 200, f"Endpoint {endpoint} failed with {response.status_code}"
    data = response.json.get("data") if isinstance(response.json.get("data"), (dict, list)) else response.json
    assert_baseline(name, data)
