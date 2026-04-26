from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_smoke_matrix_declares_lite_distributed_and_ollama_variants():
    script = (ROOT / "scripts" / "compose-test-stack.sh").read_text(encoding="utf-8")

    assert "docker-compose.base.yml" in script
    assert "docker-compose-lite.yml" in script
    assert "docker-compose.test.yml" in script
    assert "docker-compose.ollama-wsl.yml" in script
    assert "docker-compose.distributed.yml" in script
    assert "ANANTA_USE_WSL_VULKAN=0" in script
    assert "ANANTA_DISTRIBUTED=1" in script
    assert "up-distributed" in script


def test_compose_smoke_matrix_files_exist_for_release_variants():
    required = [
        "docker-compose.base.yml",
        "docker-compose-lite.yml",
        "docker-compose.test.yml",
        "docker-compose.ollama-wsl.yml",
        "docker-compose.final-tests.yml",
        "docker-compose.final-tests-openai.yml",
        "docker-compose.distributed.yml",
        "docker-compose.live-code.yml",
    ]

    missing = [name for name in required if not (ROOT / name).exists()]

    assert missing == []


def test_frontend_e2e_scripts_expose_compose_and_lite_entrypoints():
    package_json = (ROOT / "frontend-angular" / "package.json").read_text(encoding="utf-8")

    assert '"test:e2e:compose"' in package_json
    assert '"test:e2e:lite"' in package_json
    assert '"e2e:stack:up:cpu"' in package_json
    assert "ANANTA_USE_WSL_VULKAN=0" in package_json


def test_e2e_compose_backend_services_use_quickstart_single_image_dockerfile():
    for compose_name in ("docker-compose.test.yml", "docker-compose.github-ci.yml"):
        content = (ROOT / compose_name).read_text(encoding="utf-8")
        for service_name in ("ai-agent-hub", "ai-agent-alpha", "ai-agent-beta"):
            marker = f"{service_name}:"
            assert marker in content, f"{compose_name} must define {service_name}"
        assert "dockerfile: Dockerfile.quickstart-no-ollama" in content
        assert 'ANANTA_QUICKSTART_MODE: "agent-only"' in content


def test_e2e_ci_builds_backend_compose_image_from_quickstart_single_image():
    workflow = (ROOT / ".github" / "workflows" / "quality-and-docs.yml").read_text(encoding="utf-8")
    assert "file: Dockerfile.quickstart-no-ollama" in workflow


def test_final_compose_file_chains_all_tests_without_extra_profiles():
    compose = (ROOT / "docker-compose.final-tests.yml").read_text(encoding="utf-8")

    assert "service_completed_successfully" in compose
    assert "backend-test:" in compose
    assert "backend-live-llm-test:" in compose
    assert "frontend-test:" in compose
    assert "frontend-live-llm-test:" in compose
    assert "all-tests:" in compose
    assert "Dockerfile.ollama-wsl-amd" in compose
    assert "Dockerfile.compose-test" in compose


def test_openai_final_compose_file_requires_openai_key_and_chains_all_tests():
    compose = (ROOT / "docker-compose.final-tests-openai.yml").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" in compose
    assert 'DEFAULT_PROVIDER: "openai"' in compose
    assert "backend-test:" in compose
    assert "backend-live-llm-test:" in compose
    assert "frontend-test:" in compose
    assert "frontend-live-llm-test:" in compose
    assert "all-tests:" in compose
    assert "Dockerfile.compose-test" in compose
