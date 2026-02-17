from werkzeug.security import generate_password_hash

from agent.db_models import UserDB
from agent.repository import user_repo


def _login_token(client, username: str, password: str) -> str:
    res = client.post("/login", json={"username": username, "password": password})
    return res.json["data"]["access_token"]


def test_llm_benchmark_record_and_list(client):
    user_repo.save(UserDB(username="bench_admin", password_hash=generate_password_hash("pw12345"), role="admin"))
    token = _login_token(client, "bench_admin", "pw12345")
    headers = {"Authorization": f"Bearer {token}"}

    r1 = client.post(
        "/llm/benchmarks/record",
        json={
            "provider": "lmstudio",
            "model": "model-a",
            "task_kind": "coding",
            "success": True,
            "quality_gate_passed": True,
            "latency_ms": 1200,
            "tokens_total": 900,
        },
        headers=headers,
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/llm/benchmarks/record",
        json={
            "provider": "lmstudio",
            "model": "model-a",
            "task_kind": "coding",
            "success": False,
            "quality_gate_passed": False,
            "latency_ms": 2200,
            "tokens_total": 1100,
        },
        headers=headers,
    )
    assert r2.status_code == 200

    get_res = client.get("/llm/benchmarks?task_kind=coding&top_n=5", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json["data"]
    assert data["task_kind"] == "coding"
    assert isinstance(data["items"], list)
    item = next((x for x in data["items"] if x["id"] == "lmstudio:model-a"), None)
    assert item is not None
    assert item["focus"]["total"] >= 2
    assert 0 <= item["focus"]["suitability_score"] <= 100


def test_llm_benchmark_record_rejects_missing_provider_or_model(client):
    user_repo.save(UserDB(username="bench_admin_2", password_hash=generate_password_hash("pw12345"), role="admin"))
    token = _login_token(client, "bench_admin_2", "pw12345")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/llm/benchmarks/record", json={"provider": "lmstudio"}, headers=headers)
    assert res.status_code == 400
    assert res.json["message"] == "provider_and_model_required"


def test_llm_benchmark_record_forbidden_for_non_admin(client):
    user_repo.save(UserDB(username="bench_user", password_hash=generate_password_hash("pw12345"), role="user"))
    token = _login_token(client, "bench_user", "pw12345")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post(
        "/llm/benchmarks/record",
        json={"provider": "lmstudio", "model": "model-x", "task_kind": "analysis", "success": True},
        headers=headers,
    )
    assert res.status_code == 403
