from __future__ import annotations

import unittest
from textwrap import dedent

from rag_helper.extractors.adoc_extractor import AdocExtractor


class AdocExtractorTests(unittest.TestCase):
    def test_extracts_document_title_section_paths_lists_and_code_blocks(self) -> None:
        extractor = AdocExtractor()
        index_records, detail_records, relation_records, stats = extractor.parse(
            "docs/architecture.adoc",
            dedent("""
            = Order Service

            == Overview
            Intro text for the service.
            * Handles orders
            * Emits events

            === Flow
            Processing details.

            [source,java]
            ----
            orderService.process();
            ----
            """),
        )

        file_record = index_records[0]
        self.assertEqual(file_record["kind"], "adoc_file")
        self.assertEqual(file_record["title"], "Order Service")

        sections = [record for record in index_records if record["kind"] == "adoc_section"]
        self.assertEqual(len(sections), 2)

        overview = next(record for record in sections if record["title"] == "Overview")
        self.assertEqual(overview["document_title"], "Order Service")
        self.assertEqual(overview["parent_id"], file_record["id"])
        self.assertEqual(overview["section_path"], ["Overview"])
        self.assertEqual(overview["lists"], ["Handles orders", "Emits events"])
        self.assertIn("Intro text for the service.", overview["content"])

        flow = next(record for record in sections if record["title"] == "Flow")
        self.assertEqual(flow["parent_id"], overview["id"])
        self.assertEqual(flow["section_path"], ["Overview", "Flow"])
        self.assertEqual(flow["code_block_count"], 1)

        code_blocks = [record for record in detail_records if record["kind"] == "adoc_code_block"]
        self.assertEqual(len(code_blocks), 1)
        self.assertEqual(code_blocks[0]["parent_id"], flow["id"])
        self.assertIn("orderService.process();", code_blocks[0]["content"])

        self.assertTrue(any(
            record["relation"] == "child_of_section"
            for record in relation_records
        ))
        self.assertTrue(any(
            record["relation"] == "child_of_file"
            for record in relation_records
        ))
        self.assertEqual(stats["section_count"], 2)
        self.assertEqual(stats["code_block_count"], 1)

    def test_ignores_preamble_until_first_section(self) -> None:
        extractor = AdocExtractor(include_code_blocks=False)
        index_records, detail_records, relation_records, stats = extractor.parse(
            "docs/readme.adoc",
            dedent("""
            = Platform Notes

            Intro paragraph before sections.

            == Setup
            - Install dependencies
            - Run tests
            """),
        )

        sections = [record for record in index_records if record["kind"] == "adoc_section"]
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["title"], "Setup")
        self.assertEqual(sections[0]["lists"], ["Install dependencies", "Run tests"])
        self.assertFalse(any(record["kind"] == "adoc_code_block" for record in detail_records))
        self.assertEqual(stats["list_item_count"], 2)
        self.assertEqual(len(relation_records), 2)


if __name__ == "__main__":
    unittest.main()
