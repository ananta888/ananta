from __future__ import annotations

import json

from agent.services.codecompass_output_reader import get_codecompass_output_reader
from worker.retrieval.codecompass_output_reader import CodeCompassOutputReader


def test_codecompass_output_reader_supports_hub_and_standalone_paths(tmp_path):
    out = tmp_path / "cc"
    out.mkdir()
    (out / "index.jsonl").write_text("\n".join([json.dumps({"id": "idx-1", "kind": "java_type", "file": "src/A.java"}), "{bad-json}"]), encoding="utf-8")
    (out / "details.jsonl").write_text(json.dumps({"id": "det-1", "kind": "method", "file": "src/A.java"}), encoding="utf-8")

    worker_reader = CodeCompassOutputReader()
    agent_reader = get_codecompass_output_reader()
    worker_payload = worker_reader.load_from_output_dir(output_dir=out, codecompass_version="1.0.0", profile_name="java", source_scope="repo", generated_at="now")
    agent_payload = agent_reader.load_from_output_dir(output_dir=out, codecompass_version="1.0.0", profile_name="java", source_scope="repo", generated_at="now")

    assert worker_payload["manifest"]["schema"] == "codecompass_output_manifest.v1"
    assert agent_payload["manifest"]["schema"] == "codecompass_output_manifest.v1"
    assert worker_payload["standalone_compatible"] is True
    assert worker_payload["diagnostics"]["malformed_line_count"] == 1
    assert "embedding" in worker_payload["diagnostics"]["missing_outputs"]
    assert worker_payload["records"]
    assert worker_payload["records"][0]["_provenance"]["manifest_hash"] == worker_payload["manifest"]["manifest_hash"]
    assert len(agent_payload["records"]) == len(worker_payload["records"])

