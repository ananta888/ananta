from __future__ import annotations


def _login_admin(client):
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return response.json["data"]["access_token"]


def test_tdd_blueprint_policy_requires_patch_apply_approval(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    response = client.get("/teams/blueprints", headers=auth_header)
    assert response.status_code == 200
    tdd_blueprint = next(item for item in response.json["data"] if item["name"] == "TDD")
    tdd_policy = next(artifact for artifact in tdd_blueprint["artifacts"] if artifact["title"] == "TDD Default Policy")

    payload = tdd_policy["payload"]
    assert payload["verification_required"] is True
    assert payload["review_required"] is True
    assert payload["patch_apply_requires_approval"] is True
    assert payload["red_failure_is_expected"] is True


def test_tdd_blueprint_work_profile_exposes_worker_test_patch_verify_hints(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    response = client.get("/teams/blueprints/catalog", headers=auth_header)
    assert response.status_code == 200
    tdd_item = next(item for item in response.json["data"]["items"] if item["name"] == "TDD")
    hints = set((tdd_item.get("work_profile_summary") or {}).get("capability_hints") or [])

    assert "worker.test.run" in hints
    assert "worker.patch.propose" in hints
    assert "worker.verify.result" in hints
