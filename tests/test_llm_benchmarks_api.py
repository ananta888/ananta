from werkzeug.security import generate_password_hash
from unittest.mock import patch

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


def test_llm_benchmarks_timeseries(client):
    user_repo.save(UserDB(username="bench_admin_ts", password_hash=generate_password_hash("pw12345"), role="admin"))
    token = _login_token(client, "bench_admin_ts", "pw12345")
    headers = {"Authorization": f"Bearer {token}"}

    client.post(
        "/llm/benchmarks/record",
        json={
            "provider": "lmstudio",
            "model": "model-ts",
            "task_kind": "coding",
            "success": True,
            "quality_gate_passed": True,
            "latency_ms": 900,
            "tokens_total": 800,
        },
        headers=headers,
    )
    client.post(
        "/llm/benchmarks/record",
        json={
            "provider": "lmstudio",
            "model": "model-ts",
            "task_kind": "coding",
            "success": False,
            "quality_gate_passed": False,
            "latency_ms": 1500,
            "tokens_total": 1200,
        },
        headers=headers,
    )

    res = client.get(
        "/llm/benchmarks/timeseries?provider=lmstudio&model=model-ts&task_kind=coding&bucket=day&days=30",
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json["data"]
    assert data["bucket"] == "day"
    assert isinstance(data["items"], list)
    item = next((x for x in data["items"] if x["id"] == "lmstudio:model-ts"), None)
    assert item is not None
    assert isinstance(item["points"], list)
    assert len(item["points"]) >= 1
    assert 0 <= float(item["points"][0]["suitability_score"]) <= 100


def test_llm_benchmarks_timeseries_respects_retention_policy(client, app, tmp_path):
    with app.app_context():
        app.config["DATA_DIR"] = str(tmp_path)
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["benchmark_retention"] = {"max_samples": 50, "max_days": 1}
        app.config["AGENT_CONFIG"] = cfg

    user_repo.save(UserDB(username="bench_admin_ret", password_hash=generate_password_hash("pw12345"), role="admin"))
    token = _login_token(client, "bench_admin_ret", "pw12345")
    headers = {"Authorization": f"Bearer {token}"}

    with patch("agent.routes.config.time.time", return_value=1_700_000_000 - (3 * 86400)):
        client.post(
            "/llm/benchmarks/record",
            json={"provider": "lmstudio", "model": "model-ret", "task_kind": "coding", "success": True},
            headers=headers,
        )
    with patch("agent.routes.config.time.time", return_value=1_700_000_000):
        client.post(
            "/llm/benchmarks/record",
            json={"provider": "lmstudio", "model": "model-ret", "task_kind": "coding", "success": True},
            headers=headers,
        )

    with patch("agent.routes.config.time.time", return_value=1_700_000_000):
        res = client.get(
            "/llm/benchmarks/timeseries?provider=lmstudio&model=model-ret&task_kind=coding&bucket=day&days=30",
            headers=headers,
        )
    assert res.status_code == 200
    data = res.json["data"]
    assert (data.get("retention") or {}).get("max_days") == 1
    item = next((x for x in (data.get("items") or []) if x.get("id") == "lmstudio:model-ret"), None)
    assert item is not None
    points = item.get("points") or []
    assert len(points) == 1


def test_llm_benchmarks_config_endpoint_exposes_effective_settings(client, app):
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["benchmark_retention"] = {"max_samples": 20, "max_days": 0}
        cfg["benchmark_identity_precedence"] = {
            "provider_order": ["invalid", "default_provider"],
            "model_order": ["default_model", "invalid_model"],
        }
        app.config["AGENT_CONFIG"] = cfg

    user_repo.save(UserDB(username="bench_admin_cfg", password_hash=generate_password_hash("pw12345"), role="admin"))
    token = _login_token(client, "bench_admin_cfg", "pw12345")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.get("/llm/benchmarks/config", headers=headers)
    assert res.status_code == 200
    data = res.json["data"]
    retention = data.get("retention") or {}
    assert retention.get("max_samples") == 50
    assert retention.get("max_days") == 90
    precedence = data.get("identity_precedence") or {}
    assert (precedence.get("provider_order") or [])[0] == "default_provider"
    assert (precedence.get("model_order") or [])[0] == "default_model"
    assert "defaults" in data
