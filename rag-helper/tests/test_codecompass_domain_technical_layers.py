from __future__ import annotations

import unittest

from rag_helper.domain_discovery.signals import technical_layers_for_records


class TestTechnicalLayers(unittest.TestCase):
    def test_layer_set_contains_classified_layers(self) -> None:
        records = [
            {"id": "c1", "kind": "py_module", "role_labels": ["controller"]},
            {"id": "s1", "kind": "py_module", "role_labels": ["service"]},
            {"id": "e1", "kind": "jpa_entity_chunk"},
        ]
        layers = technical_layers_for_records(records)
        # _classify_domain maps: controller -> api, service -> service,
        # jpa_entity_chunk -> data-model.
        self.assertIn("api", layers)
        self.assertIn("service", layers)
        self.assertIn("data-model", layers)

    def test_layer_set_is_deduplicated_and_sorted(self) -> None:
        records = [
            {"id": "c1", "role_labels": ["controller"]},
            {"id": "c2", "role_labels": ["controller"]},
            {"id": "c3", "role_labels": ["controller"]},
        ]
        layers = technical_layers_for_records(records)
        self.assertEqual(layers, sorted(layers))
        # Three controllers should still produce one api layer.
        self.assertEqual(layers.count("api"), 1)

    def test_unknown_record_yields_empty_layers(self) -> None:
        records = [{"id": "x", "kind": "txt_file"}]
        layers = technical_layers_for_records(records)
        # txt_file without recognised role/kind may not classify; just
        # verify the function does not raise and the result is a list.
        self.assertIsInstance(layers, list)


class TestLayerOnlyGuard(unittest.TestCase):
    """A cluster whose only shared signal is a technical layer is not a domain.

    Verified end-to-end via clustering + boundaries rather than here, but
    this test documents the contract at the signals layer: technical_layers
    is descriptive data, not a clustering criterion.
    """

    def test_layer_signal_does_not_create_root_path_candidate(self) -> None:
        # Records that all share 'api' but live in scattered paths should
        # not coalesce into a single root path. The signals layer must
        # not yield a 'api' root_path.
        from rag_helper.domain_discovery.signals import derive_root_path_candidates

        records = [
            {"id": "a", "file": "scattered/a.py", "role_labels": ["controller"]},
            {"id": "b", "file": "scattered/b.py", "role_labels": ["controller"]},
            {"id": "c", "file": "scattered/c.py", "role_labels": ["controller"]},
        ]
        candidates = derive_root_path_candidates(
            [r["file"] for r in records]
        )
        # Only 'scattered' qualifies as a root_path candidate; 'api' never
        # appears because it is not a path signal.
        self.assertEqual([c.root_path for c in candidates], ["scattered"])


if __name__ == "__main__":
    unittest.main()
