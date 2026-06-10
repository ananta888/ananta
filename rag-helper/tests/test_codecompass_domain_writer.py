"""Tests for domain-discovery writer and descriptor suggestions (CCDD-013/015).

Acceptance coverage:

  CCDD-013:
    - writer.py writes domains.detected.json, domain_boundaries.jsonl,
      domain_coupling.json with stable, sorted content
    - dry-run mode writes nothing but still records the planned paths
    - existing tests for clustering/boundaries/inputs still pass

  CCDD-015:
    - opt-in flag controls whether descriptor suggestions are written
    - suggestions land in domain_descriptor_suggestions/<id>/domain.json,
      never in domains/<id>/domain.json
    - existing domains/<id>/domain.json files are left untouched
    - the suggestion uses domain_descriptor.v1 + foundation_only +
      descriptor_only (no runtime claims, no bridge_adapter_type)
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Add repo root so we can import the validator.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_RAG_HELPER_ROOT = _REPO_ROOT / "rag-helper"
if str(_RAG_HELPER_ROOT) not in sys.path:
    sys.path.insert(0, str(_RAG_HELPER_ROOT))

from devtools.validate_codecompass_domain_discovery import (
    validate_boundary_line,
    validate_coupling_payload,
    validate_payload,
)

from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.domain_discovery.descriptors import index_existing_descriptors
from rag_helper.domain_discovery.inputs import AnalysisInputs
from rag_helper.domain_discovery.writer import (
    DOMAIN_BOUNDARIES_FILENAME,
    DOMAIN_COUPLING_FILENAME,
    DOMAINS_DETECTED_FILENAME,
    DOMAIN_DESCRIPTOR_SUGGESTIONS_DIRNAME,
    DOMAIN_DESCRIPTOR_SCHEMA,
    DOMAIN_DESCRIPTOR_SUGGESTED_LIFECYCLE,
    DOMAIN_DESCRIPTOR_SUGGESTED_RUNTIME,
    run_domain_discovery,
    write_descriptor_suggestions,
    write_domain_artifacts,
)


def _build_inputs(node_count_per_domain: int = 4) -> AnalysisInputs:
    """Build a small but cross-domain coupled AnalysisInputs fixture."""
    nodes: list[dict] = []
    edges: list[dict] = []
    domains = ("identity", "billing", "rag", "orchestration", "ui")
    for domain in domains:
        for sub in range(node_count_per_domain):
            path = f"{domain}/mod_{sub}.py"
            nodes.append(
                {"id": f"{domain}.{sub}", "kind": "py_module", "file": path}
            )
        for a in range(node_count_per_domain):
            for b in range(node_count_per_domain):
                if a == b:
                    continue
                edges.append(
                    {
                        "source": f"{domain}.{a}",
                        "target": f"{domain}.{b}",
                        "type": "calls",
                    }
                )
    # Force a strong cross-domain coupling so mutual_coupling fires.
    for edge_type in ("calls", "field_type_uses", "injects_dependency"):
        edges.append(
            {"source": "identity.0", "target": "billing.0", "type": edge_type}
        )
        edges.append(
            {"source": "billing.0", "target": "identity.0", "type": edge_type}
        )
    return AnalysisInputs.from_memory(
        index_records=[],
        detail_records=[],
        relation_records=[],
        graph_nodes=nodes,
        graph_edges=edges,
        manifest={},
    )


class TestWriterArtifacts(unittest.TestCase):
    """CCDD-013: writer writes the three artifacts with stable, sorted content."""

    def _run_pipeline(self):
        inputs = _build_inputs()
        return run_domain_discovery(
            inputs, project_root="/repo",
            limits=ProcessingLimits(),
            manifest={},
        )

    def test_artifacts_are_written(self) -> None:
        result = self._run_pipeline()
        with tempfile_TmpDir() as tmp:
            written = write_domain_artifacts(result, tmp)
            self.assertEqual(
                [p.name for p in written.output_files],
                [
                    DOMAINS_DETECTED_FILENAME,
                    DOMAIN_BOUNDARIES_FILENAME,
                    DOMAIN_COUPLING_FILENAME,
                ],
            )
            self.assertTrue((Path(tmp) / DOMAINS_DETECTED_FILENAME).is_file())
            self.assertTrue((Path(tmp) / DOMAIN_BOUNDARIES_FILENAME).is_file())
            self.assertTrue((Path(tmp) / DOMAIN_COUPLING_FILENAME).is_file())

    def test_domains_detected_validates(self) -> None:
        result = self._run_pipeline()
        validation = validate_payload(result.payload)
        self.assertTrue(validation.ok, msg=validation.errors)
        self.assertGreaterEqual(len(result.payload["domains"]), 3)

    def test_coupling_payload_validates(self) -> None:
        result = self._run_pipeline()
        coupling_validation = validate_coupling_payload(result.coupling_payload)
        self.assertTrue(coupling_validation.ok, msg=coupling_validation.errors)
        self.assertGreater(result.coupling_payload["pair_count"], 0)

    def test_boundaries_jsonl_is_valid_per_line(self) -> None:
        result = self._run_pipeline()
        with tempfile_TmpDir() as tmp:
            write_domain_artifacts(result, tmp)
            boundaries_path = Path(tmp) / DOMAIN_BOUNDARIES_FILENAME
            self.assertTrue(boundaries_path.is_file())
            line_count = 0
            with boundaries_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    line = json.loads(raw)
                    validation = validate_boundary_line(line)
                    self.assertTrue(validation.ok, msg=validation.errors)
                    line_count += 1
            self.assertGreaterEqual(line_count, 1)
            self.assertEqual(line_count, len(result.boundary_warnings))

    def test_dry_run_writes_nothing(self) -> None:
        result = self._run_pipeline()
        with tempfile_TmpDir() as tmp:
            written = write_domain_artifacts(result, tmp, dry_run=True)
            for filename in (
                DOMAINS_DETECTED_FILENAME,
                DOMAIN_BOUNDARIES_FILENAME,
                DOMAIN_COUPLING_FILENAME,
            ):
                self.assertFalse((Path(tmp) / filename).exists())
            self.assertEqual(len(written.output_files), 3)
            self.assertEqual(
                written.output_files[0].name, DOMAINS_DETECTED_FILENAME
            )

    def test_repeated_writes_are_byte_stable(self) -> None:
        result_a = self._run_pipeline()
        result_b = self._run_pipeline()
        with tempfile_TmpDir() as tmp_a, tempfile_TmpDir() as tmp_b:
            write_domain_artifacts(result_a, tmp_a)
            write_domain_artifacts(result_b, tmp_b)
            self.assertEqual(
                (Path(tmp_a) / DOMAINS_DETECTED_FILENAME).read_text(
                    encoding="utf-8"
                ),
                (Path(tmp_b) / DOMAINS_DETECTED_FILENAME).read_text(
                    encoding="utf-8"
                ),
            )


class TestDescriptorSuggestions(unittest.TestCase):
    """CCDD-015: opt-in descriptor suggestions; never overwrite existing descriptors."""

    def test_suggestions_only_when_opt_in(self) -> None:
        result = run_domain_discovery(
            _build_inputs(), project_root="/repo",
            limits=ProcessingLimits(),
            manifest={},
        )
        with tempfile_TmpDir() as tmp:
            suggestions_root = Path(tmp) / DOMAIN_DESCRIPTOR_SUGGESTIONS_DIRNAME
            # Default (opt_in=False): nothing is written, descriptor_suggestions
            # stays empty.
            written_off = write_descriptor_suggestions(result, tmp, opt_in=False)
            self.assertEqual(written_off.descriptor_suggestions, [])
            self.assertFalse(suggestions_root.exists())

            # Opt-in: one suggestion per candidate lands under
            # domain_descriptor_suggestions/<id>/domain.json.
            written_on = write_descriptor_suggestions(
                result, tmp, opt_in=True
            )
            self.assertGreater(len(written_on.descriptor_suggestions), 0)
            self.assertTrue(suggestions_root.is_dir())
            for path in written_on.descriptor_suggestions:
                self.assertTrue(path.is_file())
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema"], DOMAIN_DESCRIPTOR_SCHEMA)
                self.assertEqual(
                    payload["lifecycle_status"],
                    DOMAIN_DESCRIPTOR_SUGGESTED_LIFECYCLE,
                )
                self.assertEqual(
                    payload["runtime_status"],
                    DOMAIN_DESCRIPTOR_SUGGESTED_RUNTIME,
                )
                self.assertNotIn("bridge_adapter_type", payload)

    def test_existing_domains_are_not_touched(self) -> None:
        """An existing domains/<id>/domain.json must not be overwritten."""
        result = run_domain_discovery(
            _build_inputs(), project_root="/repo",
            limits=ProcessingLimits(),
            manifest={},
        )
        with tempfile_TmpDir() as tmp:
            existing_root = Path(tmp) / "domains" / "identity"
            existing_root.mkdir(parents=True, exist_ok=True)
            existing_descriptor = {
                "schema": "domain_descriptor.v1",
                "domain_id": "identity",
                "lifecycle_status": "runtime_ready",
                "runtime_status": "approved",
                "source_paths": {
                    "code_paths": ["identity/"],
                    "docs_paths": [],
                    "rag_profiles": [],
                },
            }
            existing_path = existing_root / "domain.json"
            existing_path.write_text(
                json.dumps(existing_descriptor, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            write_descriptor_suggestions(result, tmp, opt_in=True)
            # Existing file is byte-identical (never overwritten).
            self.assertEqual(
                existing_path.read_text(encoding="utf-8"),
                json.dumps(existing_descriptor, ensure_ascii=False, indent=2),
            )
            # The suggestion lives next to it, not in domains/.
            suggestion = (
                Path(tmp)
                / DOMAIN_DESCRIPTOR_SUGGESTIONS_DIRNAME
                / "identity"
                / "domain.json"
            )
            self.assertTrue(suggestion.is_file())
            self.assertNotEqual(
                existing_path.read_text(encoding="utf-8"),
                suggestion.read_text(encoding="utf-8"),
            )

    def test_suggestion_payload_is_minimal(self) -> None:
        """Suggestions expose only static analysis evidence; no runtime claims."""
        result = run_domain_discovery(
            _build_inputs(), project_root="/repo",
            limits=ProcessingLimits(),
            manifest={},
        )
        with tempfile_TmpDir() as tmp:
            write_descriptor_suggestions(result, tmp, opt_in=True)
            for path in result.domain_candidates:
                suggestion = (
                    Path(tmp)
                    / DOMAIN_DESCRIPTOR_SUGGESTIONS_DIRNAME
                    / path.domain_id
                    / "domain.json"
                )
                payload = json.loads(suggestion.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema"], DOMAIN_DESCRIPTOR_SCHEMA)
                self.assertEqual(
                    payload["lifecycle_status"],
                    DOMAIN_DESCRIPTOR_SUGGESTED_LIFECYCLE,
                )
                self.assertEqual(
                    payload["runtime_status"],
                    DOMAIN_DESCRIPTOR_SUGGESTED_RUNTIME,
                )
                # No plugin-style fields.
                self.assertNotIn("bridge_adapter_type", payload)
                self.assertNotIn("plugin", payload)
                self.assertNotIn("imports", payload)


# Inline minimal tmp-dir helper to avoid an extra dependency.
import tempfile  # noqa: E402


class tempfile_TmpDir:
    """Context manager that yields a tmp dir path as a string."""

    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        return self._tmp.name

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
