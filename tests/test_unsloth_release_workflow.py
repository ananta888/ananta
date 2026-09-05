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
    install_backend = fake_hub_job.index("pip install -r ../requirements.lock -r ../requirements-dev.lock")
    run_playwright = fake_hub_job.index("npx playwright test tests/unsloth-model-training-fake-hub.spec.ts")

    assert setup_python < install_backend < run_playwright


def test_extracted_smoke_helpers_trigger_pull_request_and_push_gates() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    jobs_start = workflow.index("\njobs:")
    triggers = workflow[:jobs_start]

    assert triggers.count('"scripts/lora_training_smoke_*.py"') == 2
    assert triggers.count('"agent/routes/ml_intern_training*.py"') == 2


def test_gpu_job_uses_hub_issued_evidence_and_commit_bound_image() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    job_start = workflow.index("\n  gpu-unsloth-prerelease:")
    gpu_job = workflow[job_start:]

    assert "scripts/run_hub_evidence_unsloth_gpu_gate.py" in gpu_job
    assert '--build-arg "SOURCE_REVISION=${GITHUB_SHA}"' in gpu_job
    assert "src_ids:" not in workflow
    assert "run_ids:" not in workflow
    assert "runtime_image_digest:" not in workflow
    assert "production_release_eligible" in gpu_job
