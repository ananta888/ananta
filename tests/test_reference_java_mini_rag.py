from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.services.reference_profile_service import ReferenceProfileService


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "java_security_mini"


@dataclass(frozen=True)
class MiniJavaChunk:
    source_path: str
    symbol_hint: str
    text: str


def _symbol_hint(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("public final class "):
            return stripped.split("public final class ", 1)[1].split("{", 1)[0].strip()
        if stripped.startswith("public record "):
            return stripped.split("public record ", 1)[1].split("(", 1)[0].strip()
    return path.stem


def _build_mini_java_index(root: Path) -> list[MiniJavaChunk]:
    chunks: list[MiniJavaChunk] = []
    for source in sorted(root.rglob("*.java")):
        text = source.read_text(encoding="utf-8")
        chunks.append(
            MiniJavaChunk(
                source_path=str(source.relative_to(root)),
                symbol_hint=_symbol_hint(source, text),
                text=text,
            )
        )
    return chunks


def _search(chunks: list[MiniJavaChunk], query: str) -> list[MiniJavaChunk]:
    tokens = [token.lower() for token in query.replace("/", " ").replace("_", " ").split() if token.strip()]

    def score(chunk: MiniJavaChunk) -> int:
        haystack = f"{chunk.source_path}\n{chunk.symbol_hint}\n{chunk.text}".lower()
        return sum(1 for token in tokens if token in haystack)

    return sorted((chunk for chunk in chunks if score(chunk) > 0), key=lambda chunk: (-score(chunk), chunk.source_path))


def test_reference_profile_selection_prefers_keycloak_for_java_security_backend():
    service = ReferenceProfileService()

    result = service.recommend_for_flow(
        flow="new_project",
        mode_data={
            "preferred_stack": "Java",
            "project_idea": "OIDC token validation and admin authorization backend",
            "platform": "security backend service",
        },
    )

    assert result["selected_profile"]["profile_id"] == "ref.java.keycloak"
    assert result["selected_profile"]["reference_source"]["repo"] == "keycloak/keycloak"
    assert result["selected_reason"]["strategy"] == "deterministic_language_project_type_match_v1"


def test_mini_java_reference_fixture_can_be_indexed_and_retrieved():
    chunks = _build_mini_java_index(FIXTURE_ROOT)

    assert {chunk.symbol_hint for chunk in chunks} == {
        "AdminResource",
        "PolicyService",
        "TokenVerifier",
        "VerificationResult",
    }

    token_results = _search(chunks, "token validation issuer claims")
    assert token_results
    assert token_results[0].symbol_hint == "TokenVerifier"
    assert "verifyBearerToken" in token_results[0].text

    admin_results = _search(chunks, "admin api role policy")
    assert admin_results
    assert admin_results[0].symbol_hint in {"AdminResource", "PolicyService"}
    assert any(chunk.symbol_hint == "AdminResource" for chunk in admin_results)
    assert any(chunk.symbol_hint == "PolicyService" for chunk in admin_results)


def test_mini_java_reference_fixture_has_bounded_retrieval_shape():
    service = ReferenceProfileService()
    retrieval_contract = service.build_retrieval_contract()
    chunks = _build_mini_java_index(FIXTURE_ROOT)

    assert retrieval_contract["entry_points"]["mode"] == "bounded_reference_retrieval_v1"
    assert retrieval_contract["chunking_indexing_strategy"]["guardrails"]["reject_unbounded_repository_context"] is True
    assert len(chunks) <= retrieval_contract["entry_points"]["global_bounds"]["max_total_chunks"]
    assert all(chunk.source_path.endswith(".java") for chunk in chunks)
    assert all("tests/fixtures" not in chunk.source_path for chunk in chunks)
