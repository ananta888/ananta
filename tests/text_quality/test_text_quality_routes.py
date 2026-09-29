from types import SimpleNamespace

from agent.ai_agent import create_app
from agent.services.text_quality.models import (
    ContentKind,
    EvaluationStatus,
    TextQualityEvaluationResult,
)


def _app():
    app = create_app()
    app.config.update(
        TESTING=True,
        AGENT_TOKEN="",
        AGENT_CONFIG={
            "text_quality": {
                "enabled": True,
                "max_input_chars": 100,
            }
        },
    )
    return app


def test_evaluate_route_rejects_empty_and_oversize(monkeypatch):
    app = _app()
    client = app.test_client()
    assert client.post("/api/text-quality/evaluate", json={"text": ""}).status_code == 400
    assert client.post("/api/text-quality/evaluate", json={"text": "x" * 101}).status_code == 413


def test_evaluate_route_returns_bounded_contract(monkeypatch):
    import agent.routes.text_quality as route

    result = TextQualityEvaluationResult(
        evaluation_id="eval-1",
        slop_score=0.1,
        depth_score=0.8,
        style_fit_score=0.9,
        criteria_version="1",
        language="de",
        content_kind=ContentKind.FREEFORM_PROSE,
        confidence=1,
        status=EvaluationStatus.COMPLETED,
    )
    monkeypatch.setattr(
        route,
        "get_text_quality_runtime_service",
        lambda: SimpleNamespace(evaluate=lambda **_: (result, SimpleNamespace(id="eval-1"))),
    )
    app = _app()
    response = app.test_client().post(
        "/api/text-quality/evaluate",
        json={"text": "Ein konkreter und ausreichend langer Text für die Prüfung."},
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["evaluation_id"] == "eval-1"


def test_criteria_get_requires_existing_row(monkeypatch):
    import agent.routes.text_quality as route

    monkeypatch.setattr(
        route,
        "get_repository_registry",
        lambda: SimpleNamespace(
            text_quality_criteria_set_repo=SimpleNamespace(get_by_id=lambda _: None)
        ),
    )
    assert _app().test_client().get("/api/text-quality/criteria/missing").status_code == 404

