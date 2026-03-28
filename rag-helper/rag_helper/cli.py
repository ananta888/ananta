from __future__ import annotations

import argparse
from pathlib import Path

from rag_helper.application.project_processor import process_project


def run_cli(
    default_extensions: set[str],
    default_excludes: set[str],
    java_extractor_cls,
    xml_extractor_cls,
    xsd_extractor_cls,
) -> None:
    parser = argparse.ArgumentParser(
        description="Convert Java/XML/XSD project files into AST/structure-based RAG JSONL v3."
    )
    parser.add_argument("root", help="Projektverzeichnis")
    parser.add_argument("-o", "--out", default="rag_out", help="Ausgabeverzeichnis")
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=sorted(default_extensions),
        help="Dateiendungen ohne Punkt",
    )
    parser.add_argument(
        "--exclude-trivial-methods",
        action="store_true",
        help="Getter/Setter und ähnliche triviale Methoden auslassen",
    )
    parser.add_argument(
        "--no-code-snippets",
        action="store_true",
        help="Keine Code-Snippets in details.jsonl",
    )
    parser.add_argument(
        "--no-xml-node-details",
        action="store_true",
        help="Keine detaillierten XML-Node-Records erzeugen",
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve()
    extensions = {x.lower().lstrip(".") for x in args.extensions}

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Ungültiges Verzeichnis: {root}")

    process_project(
        root=root,
        out_dir=out_dir,
        extensions=extensions,
        excludes=default_excludes,
        include_code_snippets=not args.no_code_snippets,
        exclude_trivial_methods=args.exclude_trivial_methods,
        include_xml_node_details=not args.no_xml_node_details,
        java_extractor_cls=java_extractor_cls,
        xml_extractor_cls=xml_extractor_cls,
        xsd_extractor_cls=xsd_extractor_cls,
    )
