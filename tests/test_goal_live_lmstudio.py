import os

import pytest
import requests

from agent.config import settings
from agent.db_models import AgentInfoDB
from agent.repository import agent_repo
from agent.routes.tasks.autopilot import autonomous_loop
from agent.routes.tasks.utils import _get_local_task_status


LIVE_LMSTUDIO_FLAG = "RUN_LIVE_LLM_TESTS"
LIVE_LMSTUDIO_URL_ENV = "LMSTUDIO_URL"
LIVE_E2E_LMSTUDIO_URL_ENV = "E2E_LMSTUDIO_URL"
LIVE_LMSTUDIO_MODEL_ENV = "LMSTUDIO_MODEL"
DEFAULT_LMSTUDIO_URL = "http://192.168.96.1:1234/v1"


def _live_lmstudio_base_url() -> str:
    return str(
        os.environ.get(LIVE_LMSTUDIO_URL_ENV)
        or os.environ.get(LIVE_E2E_LMSTUDIO_URL_ENV)
        or DEFAULT_LMSTUDIO_URL
    ).strip()


def _live_lmstudio_models_url() -> str:
    base_url = _live_lmstudio_base_url().rstrip("/")
    if base_url.endswith("/v1"):
        return f"{base_url}/models"
    if "/v1/" in base_url:
        return f"{base_url.split('/v1/', 1)[0]}/v1/models"
    if base_url.endswith("/models"):
        return base_url
    return f"{base_url}/v1/models"


def _should_run_live_lmstudio_tests() -> bool:
    return str(os.environ.get(LIVE_LMSTUDIO_FLAG) or "").strip() == "1"


def _select_live_goal_model(models: list[dict]) -> str:
    weighted_tokens = (
        ("coder", 5),
        ("instruct", 4),
        ("chat", 4),
        ("assistant", 3),
        ("qwen", 2),
        ("deepseek", 2),
        ("llama", 1),
        ("mistral", 1),
    )
    excluded_tokens = (
        "embed",
        "embedding",
        "rerank",
        "whisper",
        "tts",
        "speech",
        "audio",
        "voxtral",
    )

    def _model_id(item: dict) -> str:
        return str(item.get("id") or "").strip()

    def _score(item: dict) -> int:
        model_id = _model_id(item).lower()
        if any(token in model_id for token in excluded_tokens):
            return -100
        return sum(weight for token, weight in weighted_tokens if token in model_id)

    candidates = [item for item in models if _model_id(item)]
    preferred = sorted((item for item in candidates if _score(item) > 0), key=_score, reverse=True)
    if preferred:
        return _model_id(preferred[0])

    fallback = [item for item in candidates if _score(item) >= 0]
    if fallback:
        return _model_id(fallback[0])

    return _model_id(candidates[0]) if candidates else ""


def _require_live_lmstudio() -> dict:
    if not _should_run_live_lmstudio_tests():
        pytest.skip(f"Requires live LM Studio backend (set {LIVE_LMSTUDIO_FLAG}=1).")

    try:
        response = requests.get(_live_lmstudio_models_url(), timeout=5)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        pytest.skip(f"LM Studio is not reachable at {_live_lmstudio_models_url()}: {exc}")

    models = list((payload or {}).get("data") or [])
    if not models:
        pytest.skip("LM Studio is reachable but returned no models.")

    requested_model = str(os.environ.get(LIVE_LMSTUDIO_MODEL_ENV) or "").strip()
    selected_model = requested_model or _select_live_goal_model(models)
    if not selected_model:
        pytest.skip("LM Studio did not return a usable model id.")

    if requested_model and not any(str(item.get("id") or "").strip() == requested_model for item in models):
        pytest.skip(f"Requested LM Studio model {requested_model!r} is not loaded.")

    return {
        "base_url": _live_lmstudio_base_url(),
        "model": selected_model,
        "models": models,
    }


@pytest.fixture
def live_lmstudio_goal_config(app):
    from agent.routes.tasks.auto_planner import auto_planner

    runtime = _require_live_lmstudio()
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["default_provider"] = "lmstudio"
        cfg["default_model"] = runtime["model"]
        cfg["llm_config"] = {
            "provider": "lmstudio",
            "base_url": runtime["base_url"],
            "model": runtime["model"],
        }
        app.config["AGENT_CONFIG"] = cfg
        provider_urls = dict(app.config.get("PROVIDER_URLS") or {})
        provider_urls["lmstudio"] = runtime["base_url"]
        app.config["PROVIDER_URLS"] = provider_urls
        auto_planner.max_subtasks_per_goal = 3
        auto_planner.llm_timeout = 20
        auto_planner.llm_retry_attempts = 1
        auto_planner.llm_retry_backoff = 0.2
    return runtime


class TestGoalLiveLMStudio:
    def test_goal_live_lmstudio_plan_preview_creates_persisted_plan(
        self, client, admin_auth_header, live_lmstudio_goal_config
    ):
        response = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "goal": (
                    "Plane ein kleines Beispielprojekt mit Python-Backend und Angular-Frontend. "
                    "Antworte mit wenigen konkreten Umsetzungsschritten."
                ),
                "team_id": "team-live-preview",
                "create_tasks": False,
                "use_template": False,
                "use_repo_context": False,
            },
        )

        assert response.status_code == 201, response.get_json()
        payload = response.get_json()["data"]
        goal = payload["goal"]
        subtasks = payload["subtasks"]

        assert goal["status"] == "planned"
        assert payload["created_task_ids"] == []
        assert payload["plan_id"]
        assert payload["plan_node_ids"]
        assert subtasks
        assert all(str(item.get("title") or "").strip() for item in subtasks)
        assert payload["workflow"]["effective"]["planning"]["create_tasks"] is False
        assert payload["workflow"]["effective"]["routing"]["team_id"] == "team-live-preview"
        assert payload["workflow"]["provenance"]["planning.create_tasks"] == "override"

        detail_res = client.get(f"/goals/{goal['id']}/detail", headers=admin_auth_header)
        assert detail_res.status_code == 200
        detail = detail_res.get_json()["data"]
        assert detail["goal"]["id"] == goal["id"]
        assert detail["plan"]["plan"]["id"] == payload["plan_id"]
        assert len(detail["plan"]["nodes"]) == len(payload["plan_node_ids"])

    def test_goal_live_lmstudio_plans_tasks_and_autopilot_executes_without_frontend(
        self, client, app, admin_auth_header, live_lmstudio_goal_config, monkeypatch
    ):
        monkeypatch.setattr(settings, "role", "hub")
        autonomous_loop.stop(persist=False)

        create_res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "goal": (
                    "Erstelle einen kleinen Zielplan fuer ein Python-Backend mit Angular-Frontend. "
                    "Nutze mehrere konkrete, umsetzbare Aufgaben."
                ),
                "team_id": "team-live-execution",
                "create_tasks": True,
                "use_template": False,
                "use_repo_context": False,
            },
        )

        assert create_res.status_code == 201, create_res.get_json()
        payload = create_res.get_json()["data"]
        goal_id = payload["goal"]["id"]
        created_ids = list(payload["created_task_ids"] or [])
        assert created_ids

        with app.app_context():
            agent_repo.save(
                AgentInfoDB(
                    url="http://worker-goal-live:5000",
                    name="worker-goal-live",
                    role="worker",
                    token="tok-goal-live",
                    status="online",
                )
            )

        def _fake_forward(worker_url, endpoint, data, token=None):
            if endpoint.endswith("/step/propose"):
                return {"status": "success", "data": {"reason": "execute goal task", "command": "echo ok"}}
            if endpoint.endswith("/step/execute"):
                return {
                    "status": "success",
                    "data": {"status": "completed", "exit_code": 0, "output": "execution success ok"},
                }
            raise AssertionError(endpoint)

        monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)

        with app.app_context():
            for _ in range(len(created_ids) + 2):
                autonomous_loop.tick_once()

        detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
        assert detail_res.status_code == 200
        detail = detail_res.get_json()["data"]
        assert detail["goal"]["id"] == goal_id
        assert detail["trace"]["task_ids"]
        assert detail["artifacts"]["result_summary"]["completed_tasks"] == len(created_ids)
        assert detail["artifacts"]["headline_artifact"]["preview"] == "execution success ok"

        for task_id in created_ids:
            task = _get_local_task_status(task_id)
            assert task is not None
            assert task["goal_id"] == goal_id
            assert task["team_id"] == "team-live-execution"
            assert task["status"] == "completed"
