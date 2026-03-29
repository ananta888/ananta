import json
import os
import time
from pathlib import Path

import pytest
import requests

from agent.config import settings
from agent.llm_benchmarks import load_benchmarks
from agent.repository import task_repo


LIVE_AGENT_CHAIN_FLAG = "RUN_LIVE_AGENT_CHAIN_E2E"
LIVE_LLM_FLAG = "RUN_LIVE_LLM_TESTS"
LIVE_LLM_PROVIDER_ENV = "LIVE_LLM_PROVIDER"
LIVE_LLM_MODEL_ENV = "LIVE_LLM_MODEL"
LIVE_LLM_DETERMINISTIC_MODEL_ENV = "LIVE_LLM_DETERMINISTIC_MODEL"
LIVE_LLM_TIMEOUT_ENV = "LIVE_LLM_TIMEOUT_SEC"
LIVE_LLM_RETRY_ATTEMPTS_ENV = "LIVE_LLM_RETRY_ATTEMPTS"
LIVE_LLM_RETRY_BACKOFF_ENV = "LIVE_LLM_RETRY_BACKOFF_SEC"
LIVE_OLLAMA_URL_ENV = "OLLAMA_URL"
LIVE_E2E_OLLAMA_URL_ENV = "E2E_OLLAMA_URL"
LIVE_OLLAMA_MODEL_ENV = "OLLAMA_MODEL"
LIVE_OLLAMA_DETERMINISTIC_MODEL_ENV = "OLLAMA_DETERMINISTIC_MODEL"
LIVE_LMSTUDIO_URL_ENV = "LMSTUDIO_URL"
LIVE_E2E_LMSTUDIO_URL_ENV = "E2E_LMSTUDIO_URL"
LIVE_LMSTUDIO_MODEL_ENV = "LMSTUDIO_MODEL"
LIVE_LMSTUDIO_DETERMINISTIC_MODEL_ENV = "LMSTUDIO_DETERMINISTIC_MODEL"
DEFAULT_OLLAMA_URL = "http://ollama:11434/api/generate"
DEFAULT_LMSTUDIO_URL = "http://localhost:1234/v1"


def _should_run_live_agent_chain_tests() -> bool:
    return str(os.environ.get(LIVE_AGENT_CHAIN_FLAG) or "").strip() == "1"


def _live_llm_provider() -> str:
    return str(os.environ.get(LIVE_LLM_PROVIDER_ENV) or "ollama").strip().lower()


def _should_run_live_llm_tests() -> bool:
    return str(os.environ.get(LIVE_LLM_FLAG) or "").strip() == "1"


def _normalize_ollama_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    suffix = "/api/generate"
    if normalized.endswith(suffix):
        return normalized[: -len(suffix)]
    return normalized


def _live_llm_base_url() -> str:
    if _live_llm_provider() == "ollama":
        return str(
            os.environ.get(LIVE_OLLAMA_URL_ENV)
            or os.environ.get(LIVE_E2E_OLLAMA_URL_ENV)
            or DEFAULT_OLLAMA_URL
        ).strip()

    return str(
        os.environ.get(LIVE_LMSTUDIO_URL_ENV)
        or os.environ.get(LIVE_E2E_LMSTUDIO_URL_ENV)
        or DEFAULT_LMSTUDIO_URL
    ).strip()


def _live_llm_models_url() -> str:
    base_url = _live_llm_base_url().rstrip("/")
    if _live_llm_provider() == "ollama":
        return f"{_normalize_ollama_base_url(base_url)}/api/tags"
    if base_url.endswith("/v1"):
        return f"{base_url}/models"
    if "/v1/" in base_url:
        return f"{base_url.split('/v1/', 1)[0]}/v1/models"
    if base_url.endswith("/models"):
        return base_url
    return f"{base_url}/v1/models"


def _requested_live_goal_model() -> str:
    return str(
        os.environ.get(LIVE_LLM_MODEL_ENV)
        or os.environ.get(
            LIVE_OLLAMA_MODEL_ENV if _live_llm_provider() == "ollama" else LIVE_LMSTUDIO_MODEL_ENV
        )
        or ""
    ).strip()


def _ollama_model_matches(requested_model: str, available_model: str) -> bool:
    requested = requested_model.strip()
    available = available_model.strip()
    if not requested or not available:
        return False
    if requested == available:
        return True
    if ":" not in requested and available.startswith(f"{requested}:"):
        return True
    return False


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
    excluded_tokens = ("embed", "embedding", "rerank", "whisper", "tts", "speech", "audio", "voxtral")

    def _model_id(item: dict) -> str:
        return str(item.get("id") or item.get("name") or "").strip()

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


def _select_deterministic_live_goal_model(models: list[dict]) -> str:
    explicit = str(
        os.environ.get(LIVE_LLM_DETERMINISTIC_MODEL_ENV)
        or os.environ.get(
            LIVE_OLLAMA_DETERMINISTIC_MODEL_ENV
            if _live_llm_provider() == "ollama"
            else LIVE_LMSTUDIO_DETERMINISTIC_MODEL_ENV
        )
        or ""
    ).strip()
    if explicit:
        return explicit
    return _select_live_goal_model(models)


def _require_live_llm() -> dict:
    if not _should_run_live_llm_tests():
        pytest.skip(f"Requires live local LLM backend (set {LIVE_LLM_FLAG}=1).")

    deadline = time.time() + float(os.environ.get("LIVE_LLM_READY_TIMEOUT_SEC") or "45")
    last_error = None
    models = []
    while time.time() < deadline:
        try:
            response = requests.get(_live_llm_models_url(), timeout=5)
            response.raise_for_status()
            payload = response.json()
            if _live_llm_provider() == "ollama":
                models = [{"id": str(item.get("name") or "").strip()} for item in list((payload or {}).get("models") or [])]
            else:
                models = list((payload or {}).get("data") or [])
            if models:
                break
            last_error = "reachable but returned no models yet"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)

    if not models:
        pytest.skip(f"Configured live LLM backend is not ready at {_live_llm_models_url()}: {last_error}")

    requested_model = _requested_live_goal_model()
    selected_model = requested_model or _select_live_goal_model(models)
    if not selected_model:
        pytest.skip("Configured live LLM backend did not return a usable model id.")

    if requested_model and not any(
        _ollama_model_matches(requested_model, str(item.get("id") or item.get("name") or "").strip())
        if _live_llm_provider() == "ollama"
        else str(item.get("id") or item.get("name") or "").strip() == requested_model
        for item in models
    ):
        pytest.skip(f"Requested live LLM model {requested_model!r} is not loaded.")

    return {
        "provider": _live_llm_provider(),
        "base_url": _live_llm_base_url(),
        "model": selected_model,
        "models": models,
    }


def _require_live_agent_chain_runtime() -> dict:
    if not _should_run_live_agent_chain_tests():
        pytest.skip(
            f"Requires live agent-chain runtime (set {LIVE_AGENT_CHAIN_FLAG}=1 and RUN_LIVE_LLM_TESTS=1)."
        )
    return _require_live_llm()


def _configure_live_runtime(app, tmp_path: Path) -> dict:
    from agent.routes.tasks.auto_planner import auto_planner

    runtime = _require_live_agent_chain_runtime()
    selected_model = _select_deterministic_live_goal_model(runtime["models"])
    llm_timeout = max(30, int(float(os.environ.get(LIVE_LLM_TIMEOUT_ENV) or "90")))
    llm_retry_attempts = max(1, int(os.environ.get(LIVE_LLM_RETRY_ATTEMPTS_ENV) or "2"))
    llm_retry_backoff = max(0.1, float(os.environ.get(LIVE_LLM_RETRY_BACKOFF_ENV) or "0.5"))

    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["default_provider"] = runtime["provider"]
        cfg["default_model"] = selected_model
        cfg["llm_config"] = {
            "provider": runtime["provider"],
            "base_url": runtime["base_url"],
            "model": selected_model,
        }
        cfg["sgpt_routing"] = {
            "policy_version": "v3",
            "default_backend": "sgpt",
            "task_kind_backend": {
                "coding": "sgpt",
                "analysis": "sgpt",
                "doc": "sgpt",
                "ops": "sgpt",
            },
        }
        cfg["command_timeout"] = max(60, llm_timeout)
        app.config["AGENT_CONFIG"] = cfg
        app.config["DATA_DIR"] = str(tmp_path)
        provider_urls = dict(app.config.get("PROVIDER_URLS") or {})
        provider_urls[runtime["provider"]] = runtime["base_url"]
        app.config["PROVIDER_URLS"] = provider_urls
        auto_planner.max_subtasks_per_goal = 3
        auto_planner.llm_timeout = llm_timeout
        auto_planner.llm_retry_attempts = llm_retry_attempts
        auto_planner.llm_retry_backoff = llm_retry_backoff

    return {**runtime, "model": selected_model}


def _build_provider_candidates(client, admin_auth_header: dict, live_model: str) -> list[str]:
    response = client.get("/api/sgpt/backends", headers=admin_auth_header)
    assert response.status_code == 200
    preflight = (response.get_json().get("data") or {}).get("preflight") or {}
    cli_backends = preflight.get("cli_backends") or {}

    candidates: list[str] = []
    codex_available = bool((cli_backends.get("codex") or {}).get("binary_available"))
    opencode_available = bool((cli_backends.get("opencode") or {}).get("binary_available"))

    if not codex_available:
        candidates.append(f"codex:{settings.codex_default_model or 'gpt-5-codex'}")
    elif not opencode_available:
        candidates.append(f"opencode:{settings.opencode_default_model or 'opencode/glm-5-free'}")

    if codex_available:
        candidates.append(f"codex:{settings.codex_default_model or 'gpt-5-codex'}")
    if opencode_available:
        candidates.append(f"opencode:{settings.opencode_default_model or 'opencode/glm-5-free'}")

    candidates.append(f"sgpt:{live_model}")
    return list(dict.fromkeys(candidates))


def _assert_comparison_fallback_shape(comparisons: dict, provider_candidates: list[str]) -> None:
    assert comparisons
    assert any(isinstance(item, dict) and not item.get("error") for item in comparisons.values())

    first = provider_candidates[0] if provider_candidates else None
    if first and isinstance(comparisons.get(first), dict) and comparisons[first].get("error"):
        assert any(
            key != first and isinstance(item, dict) and not item.get("error")
            for key, item in comparisons.items()
        )


def _task_prompt(task_id: str, task_title: str, task_description: str, instruction: str) -> str:
    title = str(task_title or "").strip()
    description = str(task_description or "").strip()
    return (
        f"Task-ID: {task_id}\n"
        f"Titel: {title}\n"
        f"Beschreibung: {description}\n\n"
        f"{instruction}\n\n"
        "Nutze genau einen sicheren Shell-Befehl ohne Semikolon, ohne &&, ohne || und ohne Redirect."
    )


def _propose_and_execute(
    client,
    admin_auth_header: dict,
    task_id: str,
    *,
    prompt: str,
    providers: list[str] | None = None,
    model: str | None = None,
) -> tuple[dict, dict]:
    propose_payload = {"prompt": prompt}
    if providers:
        propose_payload["providers"] = providers
    if model:
        propose_payload["model"] = model

    propose_res = client.post(
        f"/tasks/{task_id}/step/propose",
        json=propose_payload,
        headers=admin_auth_header,
    )
    if providers and propose_res.status_code == 502:
        pytest.skip(
            "No live CLI backend succeeded for task proposal: "
            f"{json.dumps(propose_res.get_json() or {}, ensure_ascii=False)}"
        )
    assert propose_res.status_code == 200, propose_res.get_json()
    propose_data = propose_res.get_json()["data"]
    assert propose_data.get("command") or propose_data.get("tool_calls"), propose_data

    execute_res = client.post(f"/tasks/{task_id}/step/execute", json={}, headers=admin_auth_header)
    assert execute_res.status_code == 200, execute_res.get_json()
    execute_data = execute_res.get_json()["data"]
    return propose_data, execute_data


def test_live_goal_agent_chain_creates_tasks_executes_terminal_skills_and_records_model_feedback(
    client, app, admin_auth_header, tmp_path
):
    runtime = _configure_live_runtime(app, tmp_path)

    goal_root = tmp_path / "live-agent-chain"
    marker_dir = goal_root / "workspace"
    marker_file = marker_dir / "agent-marker.txt"

    create_res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={
            "goal": (
                "Plane genau drei kurze, konkrete und unabhaengige Agenten-Aufgaben. "
                "Die Aufgaben sollen pruefen, ob ein Coding-/Ops-Agent sicher Shell-Kommandos ausfuehren kann, "
                "ein Verzeichnis anlegt, eine leere Marker-Datei anlegt und danach das Ergebnisverzeichnis auflistet. "
                f"Arbeite mit diesen Zielpfaden: Verzeichnis '{marker_dir}' und Datei '{marker_file}'."
            ),
            "team_id": "team-live-agent-chain",
            "create_tasks": True,
            "use_template": False,
            "use_repo_context": False,
        },
    )

    assert create_res.status_code == 201, create_res.get_json()
    payload = create_res.get_json()["data"]
    goal_id = payload["goal"]["id"]
    created_task_ids = list(payload.get("created_task_ids") or [])

    if not payload.get("plan_id") or len(created_task_ids) < 3:
        pytest.xfail(
            f"Live model {runtime['model']!r} did not materialize the expected 3-task chain. Payload: {payload!r}"
        )

    task_ids = created_task_ids[:3]
    with app.app_context():
        tasks = []
        for task_id in task_ids:
            task = task_repo.get_by_id(task_id)
            assert task is not None
            tasks.append(task.model_dump())

    assert all(str(task.get("title") or "").strip() for task in tasks)
    assert all(str(task.get("description") or "").strip() for task in tasks)

    provider_candidates = _build_provider_candidates(client, admin_auth_header, runtime["model"])

    first_prompt = _task_prompt(
        task_ids[0],
        tasks[0]["title"],
        tasks[0]["description"],
        f"Lege das Verzeichnis '{marker_dir}' an.",
    )
    first_propose, first_execute = _propose_and_execute(
        client,
        admin_auth_header,
        task_ids[0],
        prompt=first_prompt,
        providers=provider_candidates,
    )
    assert first_execute["status"] == "completed", first_execute
    assert marker_dir.exists() and marker_dir.is_dir()
    _assert_comparison_fallback_shape(first_propose.get("comparisons") or {}, provider_candidates)

    second_prompt = _task_prompt(
        task_ids[1],
        tasks[1]["title"],
        tasks[1]["description"],
        f"Lege die leere Datei '{marker_file}' an.",
    )
    second_propose, second_execute = _propose_and_execute(
        client,
        admin_auth_header,
        task_ids[1],
        prompt=second_prompt,
        model=runtime["model"],
    )
    assert second_execute["status"] == "completed", second_execute
    assert marker_file.exists() and marker_file.is_file()

    third_prompt = _task_prompt(
        task_ids[2],
        tasks[2]["title"],
        tasks[2]["description"],
        f"Liste den Inhalt des Verzeichnisses '{marker_dir}' auf.",
    )
    third_propose, third_execute = _propose_and_execute(
        client,
        admin_auth_header,
        task_ids[2],
        prompt=third_prompt,
        model=runtime["model"],
    )
    assert third_execute["status"] == "completed", third_execute
    assert "agent-marker.txt" in str(third_execute.get("output") or "")

    detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
    assert detail_res.status_code == 200
    detail = detail_res.get_json()["data"]
    assert detail["goal"]["id"] == goal_id
    assert set(task_ids).issubset(set(detail["trace"]["task_ids"]))
    assert detail["artifacts"]["result_summary"]["completed_tasks"] >= 3

    bench_res = client.get("/llm/benchmarks?top_n=20", headers=admin_auth_header)
    assert bench_res.status_code == 200
    bench_items = (bench_res.get_json().get("data") or {}).get("items") or []
    assert bench_items

    benchmark_db = load_benchmarks(str(tmp_path))
    executed_pairs = {
        (
            str((proposal.get("backend") or "")).strip().lower(),
            str(proposal.get("model") or runtime["model"]).strip(),
        )
        for proposal in (first_propose, second_propose, third_propose)
    }
    for backend, model in executed_pairs:
        if not backend or not model:
            continue
        model_entry = (benchmark_db.get("models") or {}).get(f"{backend}:{model}")
        assert model_entry is not None
        assert int(((model_entry.get("overall") or {}).get("total") or 0)) >= 1

    terminal_log = tmp_path / "terminal_log.jsonl"
    assert terminal_log.exists()
    terminal_lines = [json.loads(line) for line in terminal_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(str(item.get("task_id") or "") in task_ids for item in terminal_lines)
    assert any(item.get("direction") == "out" for item in terminal_lines)
    assert any(item.get("direction") == "in" for item in terminal_lines)

    with app.app_context():
        final_tasks = [task_repo.get_by_id(task_id).model_dump() for task_id in task_ids]

    assert all(task.get("status") == "completed" for task in final_tasks)
    assert first_propose.get("backend")
    assert second_propose.get("backend")
    assert third_propose.get("backend")
