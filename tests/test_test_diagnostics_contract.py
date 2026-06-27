from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_playwright_config_keeps_structured_failure_artifacts_enabled():
    config = (ROOT / "frontend-angular" / "playwright.config.ts").read_text(encoding="utf-8")

    assert "function resolveResultsDir(): string" in config
    assert "const resultsDir = resolveResultsDir();" in config
    assert "outputDir: resultsDir" in config
    assert "path.join(resultsDir, fileName)" in config
    assert "resultsPath('junit-results.xml')" in config
    assert "resultsPath('results.json')" in config
    assert "trace: retainEvidenceArtifacts ? 'on' : 'on-first-retry'" in config
    assert "screenshot: retainEvidenceArtifacts ? 'on' : 'only-on-failure'" in config
    assert "video: retainEvidenceArtifacts ? 'on' : 'retain-on-failure'" in config


def test_e2e_failure_summary_includes_project_status_retry_and_sanitized_error_context():
    script = (ROOT / "frontend-angular" / "scripts" / "e2e-failure-summary.js").read_text(encoding="utf-8")

    assert "projectName" in script
    assert "retry" in script
    assert "formatError" in script
    assert "\\x1b\\[[0-?]*[ -/]*[@-~]" in script
    assert "Failing specs:" in script
    assert "collectResultsFiles" in script
    assert "E2E_RESULTS_DIR" in script


def test_ci_uploads_e2e_diagnostics_even_when_compose_test_fails():
    workflow = (ROOT / ".github" / "workflows" / "e2e-compose.yml").read_text(encoding="utf-8")

    assert "strategy:" in workflow
    assert "max-parallel: 8" in workflow
    assert "if: always()" in workflow
    assert "frontend-angular/test-results/**" in workflow
    assert "run_e2e_compose_shard.sh" in workflow
    assert "compose-foundation-auth" in workflow
    assert "compose-observability-network" in workflow


def test_backend_coverage_uses_a_single_shard_resolver_and_pip_cache():
    workflow = (ROOT / ".github" / "workflows" / "coverage.yml").read_text(encoding="utf-8")

    assert "resolve_backend_coverage" in workflow
    assert "resolve_backend_coverage_shards.py" in workflow
    assert "--shard-count 18" in workflow
    assert "cache: pip" in workflow
    assert "fromJson(needs.resolve_backend_coverage.outputs.matrix)" in workflow
    assert "matrix.shard_name" in workflow
    assert "ananta-backend-coverage-${{ matrix.shard_name }}" in workflow
