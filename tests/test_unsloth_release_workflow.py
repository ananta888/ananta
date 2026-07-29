from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/unsloth-release-gate.yml"


def test_fake_hub_job_installs_backend_runtime_before_playwright() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    job_start = workflow.index("\n  cpu-angular-fake-hub:")
    job_end = workflow.index("\n  gpu-unsloth-prerelease:", job_start)
    fake_hub_job = workflow[job_start:job_end]

    setup_python = fake_hub_job.index("actions/setup-python@")
    install_backend = fake_hub_job.index(
        "pip install -r ../requirements.lock -r ../requirements-dev.lock"
    )
    run_playwright = fake_hub_job.index(
        "npx playwright test tests/unsloth-model-training-fake-hub.spec.ts"
    )

    assert setup_python < install_backend < run_playwright
