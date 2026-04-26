from __future__ import annotations

from agent.services.seed_blueprint_catalog import get_seed_blueprint_catalog

EXPECTED_ARTIFACT_COUNTS = {
    "Scrum": 5,
    "Scrum-OpenCode": 6,
    "Kanban": 2,
    "Research": 4,
    "Code-Repair": 4,
    "TDD": 6,
    "Security-Review": 4,
    "Release-Prep": 4,
    "Research-Evolution": 5,
}


def _login_admin(client):
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return response.json["data"]["access_token"]


def test_seed_catalog_and_runtime_reconcile_keep_expected_names_and_artifact_counts(client) -> None:
    seed_map = get_seed_blueprint_catalog().as_seed_blueprint_map()
    assert set(seed_map.keys()) == set(EXPECTED_ARTIFACT_COUNTS.keys())
    assert {name: len(list(spec.get("artifacts") or [])) for name, spec in seed_map.items()} == EXPECTED_ARTIFACT_COUNTS

    admin_token = _login_admin(client)
    response = client.get("/teams/blueprints", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    blueprints = response.json["data"]
    by_name = {item["name"]: item for item in blueprints}

    assert set(EXPECTED_ARTIFACT_COUNTS.keys()).issubset(set(by_name.keys()))
    for name, expected_count in EXPECTED_ARTIFACT_COUNTS.items():
        assert len(list((by_name[name] or {}).get("artifacts") or [])) == expected_count
