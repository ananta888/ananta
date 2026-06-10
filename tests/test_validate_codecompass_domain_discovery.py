from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from devtools.validate_codecompass_domain_discovery import (
    EXPECTED_COUPLING_SCHEMA,
    EXPECTED_SCHEMA,
    validate_boundary_line,
    validate_coupling_payload,
    validate_file,
    validate_payload,
)


def _valid_payload() -> dict:
    return {
        "schema": EXPECTED_SCHEMA,
        "project_root": "/repo",
        "generated_at": "2026-06-10T16:00:00Z",
        "inputs": {"index.jsonl": 100, "graph_nodes.jsonl": 80},
        "domains": [
            {
                "domain_id": "alpha",
                "display_name": "Alpha",
                "confidence": 0.8,
                "root_paths": ["alpha/"],
                "package_prefixes": ["com.alpha"],
                "technical_layers": ["api", "service"],
                "core_records": ["alpha/main.py"],
                "record_count": 12,
                "metrics": {
                    "internal_edge_count": 10,
                    "inbound_edge_count": 2,
                    "outbound_edge_count": 1,
                    "edge_type_counts": {"field_type_uses": 5},
                },
                "boundary_warnings": [],
                "evidence": {
                    "path_signal": {"root_paths": ["alpha/"]},
                    "package_signal": None,
                    "graph_signal": None,
                    "descriptor_signal": None,
                },
            }
        ],
        "unassigned_records": [],
        "warnings": [],
    }


class TestValidatePayload(unittest.TestCase):
    def test_valid_payload_passes(self) -> None:
        result = validate_payload(_valid_payload())
        self.assertTrue(result.ok, msg=result.errors)

    def test_wrong_schema_fails(self) -> None:
        payload = _valid_payload()
        payload["schema"] = "wrong.v1"
        result = validate_payload(payload)
        self.assertFalse(result.ok)
        self.assertTrue(any("schema mismatch" in e for e in result.errors))

    def test_missing_top_level_field_fails(self) -> None:
        payload = _valid_payload()
        del payload["project_root"]
        result = validate_payload(payload)
        self.assertFalse(result.ok)
        self.assertTrue(any("project_root" in e for e in result.errors))

    def test_duplicate_domain_id_fails(self) -> None:
        payload = _valid_payload()
        payload["domains"].append(dict(payload["domains"][0]))
        result = validate_payload(payload)
        self.assertFalse(result.ok)
        self.assertTrue(any("duplicate domain_id" in e for e in result.errors))

    def test_unsorted_domains_fail(self) -> None:
        payload = _valid_payload()
        payload["domains"].append(
            {
                **payload["domains"][0],
                "domain_id": "a-first",  # sorts before "alpha"
            }
        )
        result = validate_payload(payload)
        self.assertFalse(result.ok)
        self.assertTrue(any("not sorted" in e for e in result.errors))

    def test_confidence_out_of_range_fails(self) -> None:
        payload = _valid_payload()
        payload["domains"][0]["confidence"] = 1.5
        result = validate_payload(payload)
        self.assertFalse(result.ok)
        self.assertTrue(any("confidence" in e for e in result.errors))

    def test_unknown_warning_type_fails(self) -> None:
        payload = _valid_payload()
        payload["domains"][0]["boundary_warnings"].append(
            {
                "source_domain": "alpha",
                "target_domain": "beta",
                "warning_type": "nonsense",
                "severity": "warning",
            }
        )
        result = validate_payload(payload)
        self.assertFalse(result.ok)
        self.assertTrue(any("unknown warning_type" in e for e in result.errors))

    def test_empty_evidence_warns_but_passes(self) -> None:
        payload = _valid_payload()
        payload["domains"][0]["evidence"] = {}
        result = validate_payload(payload)
        self.assertTrue(result.ok)
        self.assertTrue(any("empty evidence" in w for w in result.warnings))

    def test_required_field_per_domain(self) -> None:
        payload = _valid_payload()
        del payload["domains"][0]["metrics"]
        result = validate_payload(payload)
        self.assertFalse(result.ok)
        self.assertTrue(any("missing required field 'metrics'" in e for e in result.errors))


class TestValidateCoupling(unittest.TestCase):
    def test_valid_coupling_passes(self) -> None:
        payload = {
            "schema": EXPECTED_COUPLING_SCHEMA,
            "pairs": [
                {"source": "alpha", "target": "beta", "edge_count": 5,
                 "edge_type_counts": {"field_type_uses": 5}},
            ],
        }
        result = validate_coupling_payload(payload)
        self.assertTrue(result.ok, msg=result.errors)

    def test_duplicate_pair_fails(self) -> None:
        payload = {
            "schema": EXPECTED_COUPLING_SCHEMA,
            "pairs": [
                {"source": "alpha", "target": "beta", "edge_count": 5,
                 "edge_type_counts": {}},
                {"source": "alpha", "target": "beta", "edge_count": 1,
                 "edge_type_counts": {}},
            ],
        }
        result = validate_coupling_payload(payload)
        self.assertFalse(result.ok)
        self.assertTrue(any("duplicate pair" in e for e in result.errors))


class TestValidateBoundaryLine(unittest.TestCase):
    def test_valid_line(self) -> None:
        line = {
            "source_domain": "alpha",
            "target_domain": "beta",
            "warning_type": "mutual_coupling",
            "severity": "warning",
        }
        result = validate_boundary_line(line)
        self.assertTrue(result.ok, msg=result.errors)

    def test_missing_field_fails(self) -> None:
        result = validate_boundary_line({"source_domain": "alpha"})
        self.assertFalse(result.ok)


class TestValidateFile(unittest.TestCase):
    def test_reads_payload_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "domains.detected.json"
            path.write_text(json.dumps(_valid_payload()))
            result = validate_file(path)
        self.assertTrue(result.ok, msg=result.errors)

    def test_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("not json")
            result = validate_file(path)
        self.assertFalse(result.ok)
        self.assertTrue(any("cannot read JSON" in e for e in result.errors))

    def test_coupling_file_dispatches_by_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coupling.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": EXPECTED_COUPLING_SCHEMA,
                        "pairs": [
                            {"source": "a", "target": "b", "edge_count": 1,
                             "edge_type_counts": {}}
                        ],
                    }
                )
            )
            result = validate_file(path)
        self.assertTrue(result.ok, msg=result.errors)

    def test_unknown_schema_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weird.json"
            path.write_text(json.dumps({"schema": "what.v1"}))
            result = validate_file(path)
        self.assertFalse(result.ok)
        self.assertTrue(any("unknown schema" in e for e in result.errors))


if __name__ == "__main__":
    unittest.main()
