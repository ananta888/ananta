from __future__ import annotations

import unittest

from rag_helper.extractors.adoc_extractor import AdocExtractor
from rag_helper.utils.embedding_text import build_embedding_text, compact_text

try:
    from rag_helper.extractors.xsd_extractor import XsdExtractor
    from rag_helper.extractors.xml_extractor import XmlExtractor
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    XsdExtractor = None
    XmlExtractor = None


class EmbeddingTextModeTests(unittest.TestCase):
    def test_helper_returns_compact_variant_when_requested(self) -> None:
        text = build_embedding_text("compact", "verbose text", "compact text")
        self.assertEqual(text, "compact text")
        self.assertEqual(compact_text("a  b   c", 20), "a b c")

    @unittest.skipUnless(XmlExtractor is not None, "lxml dependency missing")
    def test_xml_extractor_compact_mode_shortens_embedding_text(self) -> None:
        extractor = XmlExtractor(embedding_text_mode="compact")
        index_records, detail_records, _, _ = extractor.parse(
            "config.xml",
            "<root><child attr='x'>value</child></root>",
        )

        self.assertIn("XML config.xml. Root root.", index_records[0]["embedding_text"])
        self.assertIn("Attrs attr.", detail_records[0]["embedding_text"])

    @unittest.skipUnless(XsdExtractor is not None, "lxml dependency missing")
    def test_xsd_extractor_compact_mode_shortens_embedding_text(self) -> None:
        extractor = XsdExtractor(embedding_text_mode="compact")
        index_records, _, _, _ = extractor.parse(
            "schema.xsd",
            """
            <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
              <xs:simpleType name="CodeType">
                <xs:restriction base="xs:string"/>
              </xs:simpleType>
            </xs:schema>
            """,
        )

        self.assertIn("XSD schema.xsd. Root schema.", index_records[0]["embedding_text"])

    def test_adoc_extractor_compact_mode_shortens_embedding_text(self) -> None:
        extractor = AdocExtractor(embedding_text_mode="compact")
        index_records, detail_records, _, _ = extractor.parse(
            "docs/arch.adoc",
            """
            = Architecture

            == Overview
            This is a long explanation for the overall architecture.
            * first
            * second
            """,
        )

        self.assertIn("AsciiDoc docs/arch.adoc.", index_records[0]["embedding_text"])
        self.assertIn("Section Overview", index_records[1]["embedding_text"])
        self.assertIn("Section detail Overview.", detail_records[0]["embedding_text"])


if __name__ == "__main__":
    unittest.main()
