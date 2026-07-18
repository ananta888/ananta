from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

from rag_helper.domain.xml_security import (
    DEFAULT_MAX_XML_ATTRIBUTES,
    DEFAULT_MAX_XML_DEPTH,
    DEFAULT_MAX_XML_INPUT_SIZE_KB,
    DEFAULT_MAX_XML_NODES,
)

_FORBIDDEN_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class XmlSecurityError(ValueError):
    """A stable diagnostic raised when untrusted XML violates parser policy."""


@dataclass(frozen=True, slots=True)
class SecureXmlDocument:
    root: etree._Element
    node_count: int
    max_depth: int
    attribute_count: int


def _validate_limit(name: str, value: int | None) -> None:
    if value is not None and (isinstance(value, bool) or value <= 0):
        raise ValueError(f"{name}_must_be_positive")


def parse_untrusted_xml(
    text: str,
    *,
    max_input_size_kb: int | None = DEFAULT_MAX_XML_INPUT_SIZE_KB,
    max_nodes: int | None = DEFAULT_MAX_XML_NODES,
    max_depth: int | None = DEFAULT_MAX_XML_DEPTH,
    max_attributes: int | None = DEFAULT_MAX_XML_ATTRIBUTES,
) -> SecureXmlDocument:
    """Parse XML/XSD with external resources and entity expansion disabled.

    ``lxml`` does not resolve XInclude or XSD imports merely by building an
    element tree.  Combined with ``no_network``, disabled DTD loading and a
    fail-closed declaration check, this parser never dereferences document
    controlled paths or URLs.
    """

    _validate_limit("max_xml_input_size_kb", max_input_size_kb)
    _validate_limit("max_xml_nodes", max_nodes)
    _validate_limit("max_xml_depth", max_depth)
    _validate_limit("max_xml_attributes", max_attributes)
    if not isinstance(text, str):
        raise TypeError("xml_text_must_be_string")

    try:
        source = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise XmlSecurityError("xml_encoding_error") from exc
    if max_input_size_kb is not None and len(source) > max_input_size_kb * 1024:
        raise XmlSecurityError(
            f"max_xml_input_size_kb_exceeded: {len(source)} > {max_input_size_kb * 1024}"
        )
    if _FORBIDDEN_DECLARATION.search(text):
        raise XmlSecurityError("xml_dtd_or_entity_declaration_forbidden")

    parser = etree.XMLParser(
        remove_comments=True,
        recover=False,
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
        strip_cdata=False,
    )
    try:
        root = etree.fromstring(source, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise XmlSecurityError("xml_syntax_error") from exc
    if root is None:
        raise XmlSecurityError("xml_root_missing")

    node_count = 0
    observed_depth = 0
    attribute_count = 0
    stack: list[tuple[etree._Element, int]] = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        if isinstance(element, etree._Entity):
            raise XmlSecurityError("xml_entity_reference_forbidden")
        if not isinstance(element.tag, str):
            continue

        node_count += 1
        observed_depth = max(observed_depth, depth)
        attribute_count += len(element.attrib)
        if max_nodes is not None and node_count > max_nodes:
            raise XmlSecurityError(f"max_xml_nodes_exceeded: {node_count} > {max_nodes}")
        if max_depth is not None and depth > max_depth:
            raise XmlSecurityError(f"max_xml_depth_exceeded: {depth} > {max_depth}")
        if max_attributes is not None and attribute_count > max_attributes:
            raise XmlSecurityError(
                f"max_xml_attributes_exceeded: {attribute_count} > {max_attributes}"
            )
        stack.extend((child, depth + 1) for child in reversed(element))

    return SecureXmlDocument(
        root=root,
        node_count=node_count,
        max_depth=observed_depth,
        attribute_count=attribute_count,
    )
