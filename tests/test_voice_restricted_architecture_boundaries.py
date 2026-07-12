from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
ANGULAR_VOICE = ROOT / "frontend-angular" / "src" / "app" / "features" / "voice"
HUB_BOUNDARY_FILES = (
    *(ROOT / "agent" / "routes").glob("voice*.py"),
    *(ROOT / "agent" / "services").glob("voice_*.py"),
    ROOT / "agent" / "services" / "restricted_inference_contract.py",
    ROOT / "agent" / "services" / "restricted_inference_port.py",
    ROOT / "agent" / "services" / "generative_judge_worker_port.py",
)
ML_MODULES = frozenset({"torch", "transformers", "sentence_transformers", "onnxruntime", "vosk", "whisper"})


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_angular_voice_production_code_uses_only_the_hub_api_adapter() -> None:
    service = (ANGULAR_VOICE / "voice-api.service.ts").read_text(encoding="utf-8")
    production_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(ANGULAR_VOICE.glob("*.ts"))
        if not path.name.endswith(".spec.ts")
    )

    assert "HubApiCoreService" in service
    assert "HttpClient" not in production_sources
    assert "http://" not in service and "https://" not in service
    assert ":8090" not in production_sources and ":8091" not in production_sources
    assert "VOICE_RUNTIME_URL" not in production_sources
    assert "RESTRICTED_INFERENCE_WORKER_URL" not in production_sources
    for line in service.splitlines():
        if "`${hubUrl}/" in line:
            assert "`${hubUrl}/v1/voice" in line


def test_hub_voice_and_restricted_ports_do_not_import_productive_ml_frameworks() -> None:
    violations: list[str] = []
    for path in sorted(HUB_BOUNDARY_FILES):
        for module in _imports(path):
            if module.split(".", 1)[0] in ML_MODULES:
                violations.append(f"{path.relative_to(ROOT)}:{module}")
            if module.startswith("worker.runtime") or module.startswith("voice_runtime"):
                violations.append(f"{path.relative_to(ROOT)}:{module}")
    assert violations == []


def test_hub_generative_judge_is_only_a_worker_port_not_a_loopback_engine() -> None:
    source = (ROOT / "agent" / "services" / "voice_generative_judge_service.py").read_text(
        encoding="utf-8"
    )

    assert "GenerativeJudgeWorkerPort" in source
    assert "LocalGenerativeJudge" not in source
    assert "LoopbackGenerativeJudgeEngine" not in source
    assert "127.0.0.1" not in source
    assert "voice_generative_judge_endpoint" not in source
    assert "voice_generative_judge_allowed_endpoints" not in source


def test_importing_hub_wire_boundaries_does_not_load_ml_libraries() -> None:
    script = """
import sys
import agent.services.voice_provider
import agent.services.restricted_inference_contract
import agent.services.restricted_inference_port
import agent.services.generative_judge_worker_port
import agent.services.voice_generative_judge_service
for module in ('torch', 'transformers', 'sentence_transformers', 'onnxruntime', 'vosk', 'whisper'):
    assert module not in sys.modules, module
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_execution_runtimes_cannot_import_or_address_each_other() -> None:
    violations: list[str] = []
    for path in sorted((ROOT / "voice_runtime").rglob("*.py")):
        for module in _imports(path):
            if module.startswith("worker.runtime") or module.startswith("agent.services.restricted_inference"):
                violations.append(f"{path.relative_to(ROOT)}:{module}")
    for path in sorted((ROOT / "worker" / "runtime").glob("restricted_inference*.py")):
        for module in _imports(path):
            if module.startswith("voice_runtime"):
                violations.append(f"{path.relative_to(ROOT)}:{module}")
    for path in sorted((ROOT / "worker" / "runtime").glob("generative_judge*.py")):
        for module in _imports(path):
            if module.startswith("voice_runtime") or module.startswith("agent.services.restricted_inference"):
                violations.append(f"{path.relative_to(ROOT)}:{module}")
    assert violations == []


def test_restricted_worker_contains_execution_not_ananta_orchestration() -> None:
    banned_import_prefixes = (
        "agent.services.task_",
        "agent.services.worker_",
        "agent.services.goal_",
        "agent.routes",
        "voice_runtime",
    )
    violations: list[str] = []
    for path in sorted((ROOT / "worker" / "runtime").glob("restricted_inference*.py")):
        for module in _imports(path):
            if module.startswith(banned_import_prefixes):
                violations.append(f"{path.relative_to(ROOT)}:{module}")
    for path in sorted((ROOT / "worker" / "runtime").glob("generative_judge*.py")):
        for module in _imports(path):
            if module.startswith(banned_import_prefixes):
                violations.append(f"{path.relative_to(ROOT)}:{module}")
    assert violations == []


def test_hub_base_install_does_not_ship_restricted_ml_engines() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        str(item).split("[", 1)[0].split("=", 1)[0].strip().lower().replace("_", "-")
        for item in project["project"]["dependencies"]
    }
    forbidden = {"sentence-transformers", "transformers", "torch", "onnxruntime"}
    assert dependencies.isdisjoint(forbidden)

    locked = (ROOT / "requirements.lock").read_text(encoding="utf-8").lower()
    for package in forbidden:
        assert not any(
            line.startswith(f"{package}==") or line.startswith(f"{package.replace('-', '_')}==")
            for line in locked.splitlines()
        )
