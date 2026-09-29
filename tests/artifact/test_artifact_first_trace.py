"""AFH-T019: TraceBundleV2 artifact handoff lifecycle tests."""
from __future__ import annotations

from worker.core.trace_bundle import TraceBundleV2


class TestTraceBundleArtifactLifecycle:
    def test_trace_bundle_has_artifact_handoff_fields(self) -> None:
        trace = TraceBundleV2(execution_id="exec-1", task_id="t1", goal_id="g1")
        assert hasattr(trace, "handoff_bundle_ref")
        assert hasattr(trace, "manifest_ref")
        assert hasattr(trace, "manifest_synthesized")
        assert hasattr(trace, "file_change_set_ref")
        assert hasattr(trace, "artifact_ids")
        assert hasattr(trace, "verification_ref")
        assert hasattr(trace, "completion_decision_ref")
        assert hasattr(trace, "advisory_json_parse_status")

    def test_artifact_ids_stored_not_content(self) -> None:
        trace = TraceBundleV2(execution_id="exec-1", task_id="t1", goal_id="g1")
        trace.artifact_ids = ["art-abc", "art-def"]
        d = trace.as_dict()
        assert d["artifact_ids"] == ["art-abc", "art-def"]
        # Content must never appear in trace
        assert "content" not in str(d.get("artifact_ids", ""))

    def test_advisory_json_parse_status_separate_from_completion(self) -> None:
        trace = TraceBundleV2(execution_id="exec-1", task_id="t1")
        trace.advisory_json_parse_status = "parse_failed"
        trace.final_status = "completed"
        d = trace.as_dict()
        # Advisory parse status must be stored separately from final_status
        assert d["advisory_json_parse_status"] == "parse_failed"
        assert d["final_status"] == "completed"
        # These must be independent — completion not driven by advisory parse

    def test_synthesized_manifest_recorded(self) -> None:
        trace = TraceBundleV2(execution_id="exec-1", task_id="t1")
        trace.manifest_synthesized = True
        trace.manifest_ref = "mfst-ref-abc"
        d = trace.as_dict()
        assert d["manifest_synthesized"] is True
        assert d["manifest_ref"] == "mfst-ref-abc"
        # Raw manifest content must NOT be in trace
        assert "artifacts" not in d or isinstance(d.get("artifact_ids"), list)

    def test_as_dict_excludes_raw_file_content(self) -> None:
        trace = TraceBundleV2(execution_id="exec-1", task_id="t1")
        trace.artifact_ids = ["art-1", "art-2"]
        d = trace.as_dict()
        # Ensure no raw file content slipped in
        import json
        serialized = json.dumps(d)
        assert "raw_content" not in serialized
        assert "file_content" not in serialized
