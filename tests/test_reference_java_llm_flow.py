from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.services.reference_profile_service import ReferenceProfileService


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "java_security_mini"
LIVE_LLM_FLAG = "RUN_LIVE_LLM_TESTS"
LIVE_LLM_PROVIDER_ENV = "LIVE_LLM_PROVIDER"
LIVE_LLM_MODEL_ENV = "LIVE_LLM_MODEL"
LIVE_LLM_TIMEOUT_ENV = "LIVE_LLM_TIMEOUT_SEC"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


@dataclass(frozen=True)
class JavaReferenceContext:
    profile_id: str
    source_repo: str
    chunks: list[dict[str, str]]


def _load_java_reference_context() -> JavaReferenceContext:
    service = ReferenceProfileService()
    selected = service.recommend_for_flow(
        flow="new_project",
        mode_data={
            "preferred_stack": "Java",
            "project_idea": "OIDC token validation and admin authorization backend",
            "platform": "security backend service",
        },
    )["selected_profile"]

    chunks: list[dict[str, str]] = []
    for source in sorted(FIXTURE_ROOT.rglob("*.java")):
        text = source.read_text(encoding="utf-8")
        chunks.append(
            {
                "path": str(source.relative_to(FIXTURE_ROOT)),
                "symbol": source.stem,
                "text": text[:1600],
            }
        )

    return JavaReferenceContext(
        profile_id=selected["profile_id"],
        source_repo=selected["reference_source"]["repo"],
        chunks=chunks,
    )


def _build_worker_prompt(context: JavaReferenceContext) -> str:
    chunk_block = "\n\n".join(
        f"SOURCE: {chunk['path']}\nSYMBOL: {chunk['symbol']}\n{chunk['text']}" for chunk in context.chunks
    )
    return f"""
You are an Ananta worker preparing a Java backend security architecture recommendation.
Use the curated reference profile {context.profile_id} from {context.source_repo} only as guidance, not as copied code.

Task:
Recommend a small Java OIDC/security backend structure with token validation, issuer policy and admin API authorization.

Reference chunks:
{chunk_block}

Return compact JSON with keys:
- selected_reference_profile
- recommended_components
- security_tests
- boundary_notes
""".strip()


def _parse_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def test_mocked_worker_llm_uses_keycloak_profile_and_java_reference_chunks(monkeypatch):
    import agent.llm_integration as llm_integration

    captured: dict[str, str] = {}

    def fake_generate_text(prompt, provider=None, model=None, base_url=None, api_key=None, timeout=30, **kwargs):
        captured["prompt"] = prompt
        return json.dumps(
            {
                "selected_reference_profile": "ref.java.keycloak",
                "recommended_components": ["TokenVerifier", "PolicyService", "AdminResource"],
                "security_tests": ["missing token is denied", "admin role is required"],
                "boundary_notes": ["reference guidance only", "no blind copy from Keycloak"],
            }
        )

    monkeypatch.setattr(llm_integration, "generate_text", fake_generate_text)

    context = _load_java_reference_context()
    prompt = _build_worker_prompt(context)
    response = llm_integration.generate_text(prompt=prompt, provider="mock", model="mock-java-reference-worker")
    payload = _parse_json_object(response)

    assert context.profile_id == "ref.java.keycloak"
    assert payload["selected_reference_profile"] == "ref.java.keycloak"
    assert "TokenVerifier" in payload["recommended_components"]
    assert "PolicyService" in payload["recommended_components"]
    assert "AdminResource" in payload["recommended_components"]
    assert "TokenVerifier.java" in captured["prompt"]
    assert "PolicyService.java" in captured["prompt"]
    assert "AdminResource.java" in captured["prompt"]
    assert "guidance" in " ".join(payload["boundary_notes"]).lower()


def _require_live_openai_runtime() -> dict[str, str | int]:
    if str(os.environ.get(LIVE_LLM_FLAG) or "").strip() != "1":
        pytest.skip(f"Requires {LIVE_LLM_FLAG}=1.")
    provider = str(os.environ.get(LIVE_LLM_PROVIDER_ENV) or "openai").strip().lower()
    if provider != "openai":
        pytest.skip(f"Requires {LIVE_LLM_PROVIDER_ENV}=openai.")
    api_key = str(os.environ.get(OPENAI_API_KEY_ENV) or "").strip()
    if not api_key:
        pytest.skip(f"Requires {OPENAI_API_KEY_ENV}.")
    return {
        "provider": provider,
        "model": str(os.environ.get(LIVE_LLM_MODEL_ENV) or DEFAULT_OPENAI_MODEL).strip(),
        "api_key": api_key,
        "timeout": int(str(os.environ.get(LIVE_LLM_TIMEOUT_ENV) or "30").strip()),
    }


def test_live_openai_worker_llm_can_use_mini_java_reference_context():
    from agent.llm_integration import generate_text

    runtime = _require_live_openai_runtime()
    context = _load_java_reference_context()
    prompt = _build_worker_prompt(context) + "\nReturn JSON only. Keep it below 160 words."

    response = generate_text(
        prompt=prompt,
        provider="openai",
        model=str(runtime["model"]),
        api_key=str(runtime["api_key"]),
        timeout=int(runtime["timeout"]),
        temperature=0,
        max_output_tokens=220,
    )
    payload = _parse_json_object(str(response or ""))

    assert payload["selected_reference_profile"] == "ref.java.keycloak"
    joined = json.dumps(payload).lower()
    assert "token" in joined
    assert "policy" in joined or "issuer" in joined
    assert "admin" in joined
