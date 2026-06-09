"""End-to-end demo: user request → PatternPlan → render → gate → artifact (PAT-021).

This module is both a test suite and a runnable demo.  Run it with:

    pytest tests/test_pattern_e2e_demo.py -v

Each test represents one step of the full pipeline.  The shared ``tmp_path``
fixture acts as the demo workspace.

Coverage:
- PAT-021 AC1: demo produces a valid PatternPlan from a Strategy request
- PAT-021 AC2: renderer produces files and manifest deterministically
- PAT-021 AC3: pattern gate passes structural checks
- PAT-021 AC4: at least one generated test file is structurally verified
- PAT-021 AC5: artifact record is created with hashes
- PAT-021 AC6: invalid pattern and disallowed path abort cleanly
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.pattern_template_renderer import (
    PatternTemplateRenderer,
    RenderError,
    TemplateFile,
)
from agent.services.pattern_gate_service import PatternGateService
from agent.services.pattern_artifact_service import (
    PatternArtifactService,
    make_plan_hash,
)
from agent.services.pattern_proposal_normalizer import get_pattern_proposal_normalizer
from agent.services.pattern_registry import get_registry

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_ROOT = ROOT / "config" / "patterns" / "templates"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_templates(pattern_id: str, language: str) -> list[TemplateFile]:
    """Load template files from the catalog entry for a given pattern."""
    registry = get_registry()
    entry = registry.get(pattern_id)
    if entry is None:
        pytest.skip(f"pattern {pattern_id!r} not in registry — skipping demo")
    templates = []
    for t in (entry.get("templates") or []):
        path = ROOT / str(t.get("path") or "")
        if not path.exists():
            pytest.skip(f"template file missing: {t.get('path')}")
        templates.append(TemplateFile(
            template_name=str(t.get("name") or ""),
            output_path=path.name.replace(".tmpl", ""),
            content=path.read_text(encoding="utf-8"),
        ))
    return templates


def _assert_manifest_stable(
    renderer: PatternTemplateRenderer,
    plan: dict,
    templates: list[TemplateFile],
    tmp_path: Path,
) -> object:
    """AC2: Render twice and assert byte-identical manifest hash."""
    m1 = renderer.render(pattern_plan=plan, templates=templates, target_root=str(tmp_path / "run1"))
    m2 = renderer.render(pattern_plan=plan, templates=templates, target_root=str(tmp_path / "run2"))
    assert m1.manifest_sha256 == m2.manifest_sha256, \
        f"Render is not deterministic: {m1.manifest_sha256!r} != {m2.manifest_sha256!r}"
    return m1


# ---------------------------------------------------------------------------
# PAT-021 AC1 — Proposal normalization (simulates worker PatternPlan submission)
# ---------------------------------------------------------------------------

class TestAC1ProposalNormalization:
    def test_valid_python_strategy_proposal_accepted(self):
        normalizer = get_pattern_proposal_normalizer()
        registry = get_registry()
        catalogue_ids = {
            str(e.get("pattern_id") or "")
            for e in registry.list()
            if isinstance(e, dict)
        }
        proposal = normalizer.normalize(
            proposal={
                "pattern_id": "python.strategy",
                "task_kind": "coding",
                "language": "python",
                "parameters_provided": {"context_class": "Order"},
            },
            catalogue_ids=catalogue_ids,
        )
        assert proposal.accepted, f"expected accepted, got blocked: {proposal.blocked_reason}"
        assert proposal.pattern_id == "python.strategy"
        assert proposal.language == "python"

    def test_valid_java_strategy_proposal_accepted(self):
        normalizer = get_pattern_proposal_normalizer()
        registry = get_registry()
        catalogue_ids = {
            str(e.get("pattern_id") or "")
            for e in registry.list()
            if isinstance(e, dict)
        }
        proposal = normalizer.normalize(
            proposal={
                "pattern_id": "java.strategy",
                "task_kind": "coding",
                "language": "java",
                "parameters_provided": {"context_class": "Cart", "package_name": "com.example"},
            },
            catalogue_ids=catalogue_ids,
        )
        assert proposal.accepted

    def test_valid_ts_strategy_proposal_accepted(self):
        normalizer = get_pattern_proposal_normalizer()
        registry = get_registry()
        catalogue_ids = {
            str(e.get("pattern_id") or "")
            for e in registry.list()
            if isinstance(e, dict)
        }
        proposal = normalizer.normalize(
            proposal={
                "pattern_id": "ts.strategy",
                "task_kind": "coding",
                "language": "typescript",
                "parameters_provided": {"context_class": "Checkout"},
            },
            catalogue_ids=catalogue_ids,
        )
        assert proposal.accepted


# ---------------------------------------------------------------------------
# PAT-021 AC2 + AC4 — Deterministic render + structural test check
# ---------------------------------------------------------------------------

class TestAC2DeterministicRender:
    def test_python_strategy_renders_deterministically(self, tmp_path: Path):
        renderer = PatternTemplateRenderer()
        plan = {
            "pattern_id": "python.strategy",
            "language": "python",
            "parameters": {"context_class": "Order"},
        }
        templates = _load_templates("python.strategy", "python")
        manifest = _assert_manifest_stable(renderer, plan, templates, tmp_path)

        assert len(manifest.files) > 0
        paths = [f.output_path for f in manifest.files]
        # AC4: at least one test file is present and non-empty
        test_files = [
            (tmp_path / "run1" / p).read_text()
            for p in paths
            if "test" in p.lower()
        ]
        assert test_files, "No test file generated"
        assert any(text.strip() for text in test_files), "Test file is empty"

    def test_java_strategy_renders_deterministically(self, tmp_path: Path):
        renderer = PatternTemplateRenderer()
        plan = {
            "pattern_id": "java.strategy",
            "language": "java",
            "parameters": {"context_class": "Cart", "package_name": "com.example.demo"},
        }
        templates = _load_templates("java.strategy", "java")
        manifest = _assert_manifest_stable(renderer, plan, templates, tmp_path)
        assert len(manifest.files) > 0

    def test_ts_strategy_renders_deterministically(self, tmp_path: Path):
        renderer = PatternTemplateRenderer()
        plan = {
            "pattern_id": "ts.strategy",
            "language": "typescript",
            "parameters": {"context_class": "Checkout"},
        }
        templates = _load_templates("ts.strategy", "typescript")
        manifest = _assert_manifest_stable(renderer, plan, templates, tmp_path)
        assert len(manifest.files) > 0


# ---------------------------------------------------------------------------
# PAT-021 AC3 — Pattern gate passes on rendered output
# ---------------------------------------------------------------------------

class TestAC3GateChecks:
    def test_python_strategy_gate_passes_on_rendered_output(self, tmp_path: Path):
        renderer = PatternTemplateRenderer()
        templates = _load_templates("python.strategy", "python")
        output_dir = tmp_path / "gate_test"
        manifest = renderer.render(
            pattern_plan={
                "pattern_id": "python.strategy",
                "language": "python",
                "parameters": {"context_class": "Order"},
            },
            templates=templates,
            target_root=str(output_dir),
        )
        gate = PatternGateService()
        result = gate.check(
            pattern_id="python.strategy",
            language="python",
            output_files=[f.output_path for f in manifest.files],
            workspace_root=output_dir,
            require_tests=True,
        )
        assert result.passed, (
            f"Gate failed: {result.failed_checks}\n"
            + "\n".join(f"  {d.name}: {d.message}" for d in result.details if not d.passed)
        )

    def test_java_strategy_gate_passes_on_rendered_output(self, tmp_path: Path):
        renderer = PatternTemplateRenderer()
        templates = _load_templates("java.strategy", "java")
        output_dir = tmp_path / "java_gate"
        manifest = renderer.render(
            pattern_plan={
                "pattern_id": "java.strategy",
                "language": "java",
                "parameters": {"context_class": "Cart", "package_name": "com.example.demo"},
            },
            templates=templates,
            target_root=str(output_dir),
        )
        gate = PatternGateService()
        result = gate.check(
            pattern_id="java.strategy",
            language="java",
            output_files=[f.output_path for f in manifest.files],
            workspace_root=output_dir,
        )
        assert result.passed, f"Gate failed: {result.failed_checks}"


# ---------------------------------------------------------------------------
# PAT-021 AC5 — Artifact record with hashes
# ---------------------------------------------------------------------------

class TestAC5ArtifactRecord:
    def test_artifact_record_created_with_hashes(self, tmp_path: Path):
        renderer = PatternTemplateRenderer()
        templates = _load_templates("python.strategy", "python")
        output_dir = tmp_path / "artifact_test"
        manifest = renderer.render(
            pattern_plan={
                "pattern_id": "python.strategy",
                "language": "python",
                "parameters": {"context_class": "Payment"},
            },
            templates=templates,
            target_root=str(output_dir),
        )
        plan_hash = make_plan_hash(
            "python.strategy", "python", {"context_class": "Payment"}
        )
        svc = PatternArtifactService(artifacts_root=tmp_path / "artifacts")
        record = svc.record(
            pattern_id="python.strategy",
            language="python",
            plan_hash=plan_hash,
            template_hash=manifest.manifest_sha256,
            generated_files=[
                {"role": f.template_name, "path": f.output_path, "sha256": f.sha256, "size_bytes": f.bytes_written}
                for f in manifest.files
            ],
        )
        assert record.artifact_id.startswith("pat-")
        assert record.plan_hash == plan_hash
        assert len(record.generated_files) == len(manifest.files)
        for gf in record.generated_files:
            assert len(gf.sha256) == 64, "sha256 must be 64-char hex"

        fetched = svc.get(plan_hash)
        assert fetched is not None
        assert fetched.artifact_id == record.artifact_id
        # AC5: artifact JSON contains all hashes
        artifact_json = (tmp_path / "artifacts" / f"{record.artifact_id}.json").read_text()
        d = json.loads(artifact_json)
        assert d["schema"] == "pattern_artifact.v1"
        assert all(len(f["sha256"]) == 64 for f in d["generated_files"])


# ---------------------------------------------------------------------------
# PAT-021 AC6 — Error cases abort cleanly
# ---------------------------------------------------------------------------

class TestAC6ErrorCases:
    def test_unknown_pattern_id_rejected_by_normalizer(self):
        normalizer = get_pattern_proposal_normalizer()
        proposal = normalizer.normalize(
            proposal={
                "pattern_id": "imaginary.unicorn_factory",
                "task_kind": "coding",
                "language": "python",
                "parameters_provided": {},
            },
            catalogue_ids={"python.strategy", "java.strategy"},
        )
        assert not proposal.accepted
        assert proposal.blocked_reason is not None

    def test_path_traversal_in_output_path_raises_render_error(self, tmp_path: Path):
        renderer = PatternTemplateRenderer()
        dangerous_template = TemplateFile(
            template_name="malicious",
            output_path="../../../etc/evil.py",
            content="# harmless",
        )
        with pytest.raises(RenderError):
            renderer.render(
                pattern_plan={
                    "pattern_id": "python.strategy",
                    "language": "python",
                    "parameters": {"context_class": "X"},
                },
                templates=[dangerous_template],
                target_root=str(tmp_path),
            )

    def test_missing_required_parameter_raises_render_error(self, tmp_path: Path):
        renderer = PatternTemplateRenderer()
        template_with_missing_var = TemplateFile(
            template_name="proto",
            output_path="proto.py",
            content="class @@context_class@@Strategy: ...\nmodule = @@missing_var@@",
        )
        with pytest.raises(RenderError):
            renderer.render(
                pattern_plan={
                    "pattern_id": "python.strategy",
                    "language": "python",
                    "parameters": {"context_class": "Order"},  # missing_var not provided
                },
                templates=[template_with_missing_var],
                target_root=str(tmp_path),
            )

    def test_absolute_output_path_raises_render_error(self, tmp_path: Path):
        renderer = PatternTemplateRenderer()
        absolute_template = TemplateFile(
            template_name="bad",
            output_path="/etc/output.py",
            content="# bad",
        )
        with pytest.raises(RenderError):
            renderer.render(
                pattern_plan={
                    "pattern_id": "python.strategy",
                    "language": "python",
                    "parameters": {"context_class": "X"},
                },
                templates=[absolute_template],
                target_root=str(tmp_path),
            )
