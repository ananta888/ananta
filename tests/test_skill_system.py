"""Tests for worker/core/skill_manifest.py (EW-T032 through EW-T037)."""
import time
import pytest
from pydantic import ValidationError

from worker.core.execution_envelope import (
    ApprovalRef, CapabilityGrant, ExecutionEnvelope, ToolPolicy
)
from worker.core.skill_manifest import (
    BASELINE_SKILLS,
    SkillManifest,
    SkillRegistry,
    SkillRegistryEntry,
    SkillProposalArtifact,
    SkillReviewArtifact,
    SkillReviewFinding,
    SkillReviewer,
    SkillRunner,
    build_baseline_registry,
)


def _env(**overrides) -> ExecutionEnvelope:
    defaults = dict(
        task_id="t1", actor_ref="hub:test",
        capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]),
        context_envelope_ref="ctx:1", audit_correlation_id="audit:1",
    )
    defaults.update(overrides)
    return ExecutionEnvelope(**defaults)


# ── EW-T032: SkillManifest ────────────────────────────────────────────────────

class TestSkillManifest:
    def test_valid_manifest(self):
        m = SkillManifest(
            id="my_skill", name="My Skill", version="1.0.0",
            capability_requirements=["planning"],
        )
        assert m.id == "my_skill"

    def test_unknown_capability_rejected(self):
        with pytest.raises(ValidationError, match="unknown capability classes"):
            SkillManifest(
                id="s", name="S", version="1.0",
                capability_requirements=["hack_the_planet"],
            )

    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            SkillManifest(id="", name="S", version="1.0")

    def test_capabilities_granted_by_all_present(self):
        m = SkillManifest(id="s", name="S", version="1.0",
                          capability_requirements=["planning", "code_read"])
        env = _env(capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]))
        assert m.capabilities_granted_by(env) is True

    def test_capabilities_granted_by_missing(self):
        m = SkillManifest(id="s", name="S", version="1.0",
                          capability_requirements=["shell_execute"])
        env = _env()
        assert m.capabilities_granted_by(env) is False

    def test_as_catalog_entry(self):
        m = SkillManifest(id="s", name="S", version="1.0", risk_class="low")
        entry = m.as_catalog_entry()
        assert "id" in entry and "risk_class" in entry


# ── EW-T033: SkillRegistry ────────────────────────────────────────────────────

class TestSkillRegistry:
    def setup_method(self):
        self.registry = SkillRegistry()
        self.registry.register(SkillRegistryEntry(
            manifest=SkillManifest(id="s1", name="S1", version="1.0",
                                   capability_requirements=["planning"]),
            content_hash="abc123",
        ))

    def test_get_registered(self):
        assert self.registry.get("s1") is not None

    def test_get_unregistered_none(self):
        assert self.registry.get("ghost") is None

    def test_enabled_skills(self):
        assert len(self.registry.enabled_skills()) == 1

    def test_disable_skill(self):
        self.registry.disable("s1")
        assert len(self.registry.enabled_skills()) == 0

    def test_disable_nonexistent_returns_false(self):
        assert self.registry.disable("ghost") is False

    def test_skills_for_envelope_matches(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["planning"]))
        skills = self.registry.skills_for_envelope(env)
        assert len(skills) == 1

    def test_skills_for_envelope_no_match(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["test_run"]))
        skills = self.registry.skills_for_envelope(env)
        assert skills == []

    def test_content_hash_update(self):
        entry = self.registry.get("s1")
        old_hash = entry.content_hash
        entry.update_source("new source code here")
        assert entry.content_hash != old_hash

    def test_trace_info_has_required_fields(self):
        entry = self.registry.get("s1")
        info = entry.trace_info()
        assert "skill_id" in info and "content_hash" in info

    def test_catalog_returns_list(self):
        catalog = self.registry.catalog()
        assert isinstance(catalog, list) and len(catalog) == 1


# ── EW-T034: SkillRunner ─────────────────────────────────────────────────────

class TestSkillRunner:
    def setup_method(self):
        self.registry = SkillRegistry()
        self.registry.register(SkillRegistryEntry(
            manifest=SkillManifest(
                id="plan_skill", name="Plan", version="1.0",
                capability_requirements=["planning"],
                allowed_tools=["read_file"],
            )
        ))
        self.runner = SkillRunner(self.registry)

    def test_run_with_granted_capabilities(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]),
            tool_policy=ToolPolicy(allowed_tool_ids=["read_file"]),
        )
        result = self.runner.run("plan_skill", env)
        assert result.success is True

    def test_run_missing_capability_denied(self):
        env = _env(capability_grant=CapabilityGrant(capabilities=["test_run"]))
        result = self.runner.run("plan_skill", env)
        assert result.success is False
        assert result.reason_code == "missing_capability"

    def test_run_unknown_skill_denied(self):
        env = _env()
        result = self.runner.run("ghost_skill", env)
        assert result.success is False
        assert result.reason_code == "skill_not_found"

    def test_run_disabled_skill_denied(self):
        self.registry.disable("plan_skill")
        env = _env()
        result = self.runner.run("plan_skill", env)
        assert result.success is False
        assert result.reason_code == "skill_disabled"

    def test_run_tool_not_in_policy_denied(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["planning"]),
            tool_policy=ToolPolicy(allowed_tool_ids=["other_tool"]),
        )
        result = self.runner.run("plan_skill", env)
        assert result.success is False
        assert result.reason_code == "tool_unavailable"

    def test_trace_info_recorded_on_success(self):
        env = _env(
            capability_grant=CapabilityGrant(capabilities=["planning", "code_read"]),
            tool_policy=ToolPolicy(allowed_tool_ids=["read_file"]),
        )
        result = self.runner.run("plan_skill", env)
        assert result.trace_info.get("skill_id") == "plan_skill"

    def test_runner_never_creates_own_authority(self):
        # The runner only checks envelope, never expands capabilities
        env_no_cap = _env(capability_grant=CapabilityGrant(capabilities=[]))
        result = self.runner.run("plan_skill", env_no_cap)
        assert result.success is False


# ── EW-T035: SkillProposalArtifact ───────────────────────────────────────────

class TestSkillProposalArtifact:
    def test_auto_enabled_always_false(self):
        manifest = SkillManifest(id="s", name="S", version="1.0")
        proposal = SkillProposalArtifact(
            artifact_id="a1", task_id="t1",
            proposed_manifest=manifest,
            rationale="because",
        )
        assert proposal.auto_enabled is False

    def test_as_dict_has_required_fields(self):
        manifest = SkillManifest(id="s", name="S", version="1.0")
        proposal = SkillProposalArtifact(
            artifact_id="a1", task_id="t1",
            proposed_manifest=manifest, rationale="r",
        )
        d = proposal.as_dict()
        assert d["kind"] == "skill_proposal_artifact"
        assert d["auto_enabled"] is False
        assert "proposed_skill_id" in d


# ── EW-T036: SkillReviewer ────────────────────────────────────────────────────

class TestSkillReviewer:
    def setup_method(self):
        self.reviewer = SkillReviewer()

    def test_stale_skill_detected(self):
        registry = SkillRegistry()
        registry.register(SkillRegistryEntry(
            manifest=SkillManifest(id="s", name="S", version="1.0"),
            content_hash="",  # no hash → stale
        ))
        artifact = self.reviewer.review(registry, task_id="t1", artifact_id="a1")
        assert any(e.finding == SkillReviewFinding.stale for e in artifact.entries)

    def test_unsafe_high_risk_shell_detected(self):
        registry = SkillRegistry()
        registry.register(SkillRegistryEntry(
            manifest=SkillManifest(
                id="s", name="S", version="1.0",
                capability_requirements=["shell_execute"],
                risk_class="high",
            ),
            content_hash="abc",
        ))
        artifact = self.reviewer.review(registry, task_id="t1", artifact_id="a1")
        assert any(e.finding == SkillReviewFinding.unsafe for e in artifact.entries)

    def test_pinned_skill_not_modifiable(self):
        registry = SkillRegistry()
        registry.register(SkillRegistryEntry(
            manifest=SkillManifest(
                id="core", name="Core", version="1.0", pinned=True
            ),
        ))
        artifact = self.reviewer.review(registry, task_id="t1", artifact_id="a1")
        assert len(artifact.pinned_skill_warnings) == 1
        assert "core" in artifact.pinned_skill_warnings[0]
        # No entries for pinned skills
        assert not any(e.skill_id == "core" for e in artifact.entries)

    def test_as_dict_has_required_fields(self):
        registry = SkillRegistry()
        artifact = self.reviewer.review(registry, task_id="t1", artifact_id="a1")
        d = artifact.as_dict()
        assert d["kind"] == "skill_review_artifact"
        assert "entries" in d and "pinned_warnings" in d


# ── EW-T037: Baseline skills ─────────────────────────────────────────────────

class TestBaselineSkills:
    def test_all_baseline_skills_have_manifests(self):
        assert len(BASELINE_SKILLS) >= 5

    def test_baseline_registry_loadable(self):
        registry = build_baseline_registry()
        assert len(registry.enabled_skills()) == len(BASELINE_SKILLS)

    def test_baseline_skills_minimal_capabilities(self):
        for m in BASELINE_SKILLS:
            assert len(m.capability_requirements) <= 4, \
                f"{m.id} declares too many capabilities"

    def test_all_baseline_skill_ids_unique(self):
        ids = [m.id for m in BASELINE_SKILLS]
        assert len(ids) == len(set(ids))

    def test_baseline_skills_no_high_risk(self):
        for m in BASELINE_SKILLS:
            assert m.risk_class in ("low", "medium"), \
                f"baseline skill {m.id!r} has unexpected risk_class {m.risk_class!r}"

    def test_baseline_skills_runnable_from_read_envelope(self):
        registry = build_baseline_registry()
        runner = SkillRunner(registry)
        env = _env(
            capability_grant=CapabilityGrant(capabilities=[
                "planning", "code_read", "patch_propose", "verify",
            ]),
            tool_policy=ToolPolicy(allowed_tool_ids=[
                "read_file", "list_directory", "propose_patch", "memory_read",
            ]),
        )
        for skill in BASELINE_SKILLS:
            result = runner.run(skill.id, env)
            assert result.success is True, \
                f"baseline skill {skill.id!r} failed: {result.reason_code}"
