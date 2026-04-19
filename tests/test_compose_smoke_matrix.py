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
