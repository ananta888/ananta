from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_quickstart_dockerfile_uses_role_entrypoint_and_exposes_fullstack_ports() -> None:
    dockerfile = (ROOT / "Dockerfile.quickstart-no-ollama").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["/app/scripts/quickstart-single-image-entrypoint.sh"]' in dockerfile
    assert "EXPOSE 5000 5001 4200 8080" in dockerfile
    assert "services/evolver_bridge" in dockerfile
    assert "opencode-ai@" in dockerfile
    assert "ollama/ollama" not in dockerfile


def test_quickstart_entrypoint_supports_single_image_roles_and_openai_guard() -> None:
    entrypoint = (ROOT / "scripts" / "quickstart-single-image-entrypoint.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in entrypoint
    assert "ANANTA_QUICKSTART_MODE" in entrypoint
    assert "ANANTA_QUICKSTART_ROLE" in entrypoint
    assert "single-container" in entrypoint
    assert "agent-only" in entrypoint
    assert "evolver_bridge" in entrypoint
    assert "deerflow_runner" in entrypoint
    assert "ml_intern_runner" in entrypoint
    assert "DEFAULT_PROVIDER=openai requires OPENAI_API_KEY" in entrypoint


def test_readme_documents_single_image_fullstack_path() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docker-compose.single-image-fullstack.yml" in readme
    assert "Evolver" in readme
    assert "DeerFlow" in readme
    assert "ml-intern" in readme
