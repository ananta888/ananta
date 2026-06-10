from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag_helper.domain_discovery.contracts import DomainCandidate
from rag_helper.domain_discovery.descriptors import (
    WARNING_DESCRIPTOR_MISMATCH,
    ExistingDescriptor,
    apply_descriptor_signal,
    build_descriptor_mismatches,
    index_existing_descriptors,
)


def _make_candidate(domain_id: str, *root_paths: str) -> DomainCandidate:
    return DomainCandidate(
        domain_id=domain_id,
        display_name=domain_id,
        confidence=0.5,
        root_paths=list(root_paths),
        record_count=4,
    )


def _make_descriptor(
    domain_id: str,
    code_paths: list[str],
    *,
    descriptor_path: str = "/p/dom/domain.json",
) -> ExistingDescriptor:
    return ExistingDescriptor(
        domain_id=domain_id,
        descriptor_path=descriptor_path,
        raw={"domain_id": domain_id, "source_paths": {"code_paths": code_paths}},
        code_paths=code_paths,
        docs_paths=[],
        rag_profiles=[],
    )


class TestIndexExistingDescriptors(unittest.TestCase):
    def test_finds_domain_json_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "domains" / "alpha").mkdir(parents=True)
            (root / "domains" / "beta").mkdir(parents=True)
            (root / "domains" / "alpha" / "domain.json").write_text(
                json.dumps(
                    {
                        "schema": "domain_descriptor.v1",
                        "domain_id": "alpha",
                        "source_paths": {
                            "code_paths": ["alpha/"],
                            "docs_paths": ["docs/alpha.adoc"],
                            "rag_profiles": [{"name": "default"}],
                        },
                    }
                )
            )
            (root / "domains" / "beta" / "domain.json").write_text(
                json.dumps(
                    {
                        "schema": "domain_descriptor.v1",
                        "domain_id": "beta",
                        "source_paths": {"code_paths": []},
                    }
                )
            )
            out = index_existing_descriptors(root)
        self.assertEqual(set(out.keys()), {"alpha", "beta"})
        self.assertEqual(out["alpha"].code_paths, ["alpha/"])
        self.assertEqual(out["alpha"].docs_paths, ["docs/alpha.adoc"])
        self.assertEqual(len(out["alpha"].rag_profiles), 1)
        self.assertEqual(out["beta"].code_paths, [])

    def test_missing_domains_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = index_existing_descriptors(tmp)
        self.assertEqual(out, {})

    def test_invalid_json_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "domains" / "alpha" / "domain.json").parent.mkdir(parents=True)
            (root / "domains" / "alpha" / "domain.json").write_text("not json")
            out = index_existing_descriptors(root)
        self.assertEqual(out, {})


class TestBuildDescriptorMismatches(unittest.TestCase):
    def test_matching_descriptor_produces_no_warnings(self) -> None:
        candidates = [_make_candidate("alpha", "alpha/")]
        descriptors = {"alpha": _make_descriptor("alpha", ["alpha/"])}
        warnings = build_descriptor_mismatches(descriptors, candidates)
        self.assertEqual(warnings, [])

    def test_descriptor_for_missing_cluster_warns(self) -> None:
        candidates = [_make_candidate("alpha", "alpha/")]
        descriptors = {
            "ghost": _make_descriptor("ghost", ["ghost/"], descriptor_path="/p/ghost/domain.json")
        }
        warnings = build_descriptor_mismatches(descriptors, candidates)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["source_domain"], "ghost")
        self.assertEqual(warnings[0]["evidence"]["kind"], "no_matching_cluster")
        self.assertEqual(warnings[0]["warning_type"], WARNING_DESCRIPTOR_MISMATCH)

    def test_descriptor_paths_under_different_root_warns(self) -> None:
        candidates = [_make_candidate("alpha", "alpha/")]
        descriptors = {"alpha": _make_descriptor("alpha", ["other/path/"])}
        warnings = build_descriptor_mismatches(descriptors, candidates)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["evidence"]["kind"], "paths_named_but_empty")


class TestApplyDescriptorSignal(unittest.TestCase):
    def test_evidence_stamped_when_descriptor_matches(self) -> None:
        candidate = _make_candidate("alpha", "alpha/")
        descriptors = {"alpha": _make_descriptor("alpha", ["alpha/"])}
        apply_descriptor_signal([candidate], descriptors)
        self.assertIsNotNone(candidate.evidence["descriptor_signal"])
        self.assertEqual(
            candidate.evidence["descriptor_signal"]["code_paths"], ["alpha/"]
        )

    def test_unknown_descriptor_does_not_mutate(self) -> None:
        candidate = _make_candidate("alpha", "alpha/")
        descriptors = {"ghost": _make_descriptor("ghost", ["ghost/"])}
        apply_descriptor_signal([candidate], descriptors)
        self.assertNotIn("descriptor_signal", candidate.evidence or {})


if __name__ == "__main__":
    unittest.main()
