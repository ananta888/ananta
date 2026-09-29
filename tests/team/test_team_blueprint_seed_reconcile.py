from __future__ import annotations

from agent.services.seed_blueprint_catalog import get_seed_blueprint_catalog
from tests_support import admin_login_token as _login_admin

# Every blueprint the shipped catalog contains, with its artifact count.
# The catalog had grown by eleven entries while this list stayed at ten, so
# the test was asserting against a catalog that no longer exists.
EXPECTED_ARTIFACT_COUNTS = {
    "Architecture-Governance": 3,
    "Code-Repair": 4,
    "Enterprise-Product-Delivery-Scrum": 3,
    "Kanban": 2,
    "Lean-Company-Direction": 2,
    "Lean-Delivery-Cell": 2,
    "Lean-Discovery": 2,
    "Lean-Enablement": 2,
    "Platform-DevOps-SRE": 3,
    "Portfolio-Product-Coordination": 3,
    "Proof-Of-Concept": 3,
    "Quality-Security-Release": 3,
    "Release-Prep": 4,
    "Research": 4,
    "Research-And-Discovery": 3,
    "Research-Evolution": 5,
    "Scrum": 5,
    "Scrum-OpenCode": 6,
    "Security-Review": 4,
    "Story-Domain-Implementation": 5,
    "TDD": 6,
}
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
