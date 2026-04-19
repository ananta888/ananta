from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_playwright_config_keeps_structured_failure_artifacts_enabled():
    config = (ROOT / "frontend-angular" / "playwright.config.ts").read_text(encoding="utf-8")

    assert "['junit', { outputFile: 'test-results/junit-results.xml' }]" in config
    assert "['json', { outputFile: 'test-results/results.json' }]" in config
    assert "trace: 'on-first-retry'" in config
    assert "screenshot: 'only-on-failure'" in config
    assert "video: 'retain-on-failure'" in config


def test_e2e_failure_summary_includes_project_status_retry_and_sanitized_error_context():
    script = (ROOT / "frontend-angular" / "scripts" / "e2e-failure-summary.js").read_text(encoding="utf-8")

    assert "projectName" in script
    assert "retry" in script
    assert "formatError" in script
    assert "\\x1b\\[[0-?]*[ -/]*[@-~]" in script
    assert "Failing specs:" in script


def test_ci_uploads_e2e_diagnostics_even_when_compose_test_fails():
    workflow = (ROOT / ".github" / "workflows" / "quality-and-docs.yml").read_text(encoding="utf-8")

    assert "continue-on-error: true" in workflow
    assert "if: always()" in workflow
    assert "frontend-angular/test-results/junit-results.xml" in workflow
    assert "frontend-angular/test-results/results.json" in workflow
    assert "frontend-angular/test-results/failure-summary.md" in workflow
