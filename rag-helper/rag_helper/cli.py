from __future__ import annotations

import argparse
from pathlib import Path

from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Wert muss > 0 sein")
    return parsed


def run_cli(
    default_extensions: set[str],
    default_excludes: set[str],
    java_extractor_cls,
    adoc_extractor_cls,
    xml_extractor_cls,
    xsd_extractor_cls,
) -> None:
    parser = argparse.ArgumentParser(
        description="Convert Java/XML/XSD/AsciiDoc project files into AST/structure-based RAG JSONL v3."
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
    parser.add_argument(
        "--include-glob",
        action="append",
        default=[],
        help="Nur Dateien verarbeiten, deren relativer Pfad auf dieses Glob-Muster passt",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Dateien mit passendem relativem Pfad-Glob zusätzlich ausschließen",
    )
    parser.add_argument(
        "--max-file-size-kb",
        type=positive_int,
        help="Dateien oberhalb dieser Größe in KB überspringen",
    )
    parser.add_argument(
        "--max-xml-nodes",
        type=positive_int,
        help="XML/XSD-Dateien mit mehr Knoten überspringen",
    )
    parser.add_argument(
        "--max-methods-per-class",
        type=positive_int,
        help="Maximalzahl extrahierter Methoden pro Java-Typ",
    )
    parser.add_argument(
        "--max-records-per-file",
        type=positive_int,
        help="Dateien überspringen, wenn mehr Records entstehen würden",
    )
    parser.add_argument(
        "--xml-mode",
        choices=("all", "config-only", "smart"),
        default="all",
        help="XML-Verarbeitung: alle Dateien, nur Config/XML oder heuristisch smart",
    )
    parser.add_argument(
        "--xml-repetitive-child-threshold",
        type=positive_int,
        default=25,
        help="Ab wie vielen gleichartigen Root-Kindern eine XML als repetitive Daten-XML gilt",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Nur geänderte Dateien neu parsen und sonst den Cache wiederverwenden",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Incremental-Cache ignorieren und vollständig neu aufbauen",
    )
    parser.add_argument(
        "--cache-file",
        default=".code_to_rag_cache.json",
        help="Pfad zur Cache-Datei für Incremental-Läufe",
    )
    parser.add_argument(
        "--generated-code-mode",
        choices=("off", "mark", "exclude"),
        default="mark",
        help="Generierten Code ignorieren, markieren oder komplett ausschließen",
    )
    parser.add_argument(
        "--generated-comment-marker",
        action="append",
        default=[],
        help="Zusätzlicher Markertext zur Erkennung generierten Codes",
    )
    parser.add_argument(
        "--no-resolve-wildcard-imports",
        action="store_true",
        help="Wildcard-Imports nicht über bekannte Package-Typen auflösen",
    )
    parser.add_argument(
        "--no-mark-import-conflicts",
        action="store_true",
        help="Mehrdeutige Typauflösungen nicht explizit markieren",
    )
    parser.add_argument(
        "--no-resolve-method-targets",
        action="store_true",
        help="Keine heuristische Zielauflösung für Methodenaufrufe erzeugen",
    )
    parser.add_argument(
        "--no-resolve-framework-relations",
        action="store_true",
        help="Keine heuristischen Spring- und JPA-Relations aus Annotationen erzeugen",
    )
    parser.add_argument(
        "--embedding-text-mode",
        choices=("verbose", "compact"),
        default="verbose",
        help="Steuert, wie ausführlich embedding_text-Felder erzeugt werden",
    )
    parser.add_argument(
        "--retrieval-output-mode",
        choices=("legacy", "split", "both"),
        default="legacy",
        help="Steuert, ob zusätzlich embedding/context JSONL-Dateien erzeugt werden",
    )
    parser.add_argument(
        "--importance-scoring-mode",
        choices=("off", "basic"),
        default="basic",
        help="Steuert, ob Index- und Retrieval-Records einen Importance-Score erhalten",
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve()
    extensions = {x.lower().lstrip(".") for x in args.extensions}
    limits = ProcessingLimits(
        max_file_size_kb=args.max_file_size_kb,
        max_xml_nodes=args.max_xml_nodes,
        max_methods_per_class=args.max_methods_per_class,
        max_records_per_file=args.max_records_per_file,
        xml_mode=args.xml_mode,
        xml_repetitive_child_threshold=args.xml_repetitive_child_threshold,
        generated_code_mode=args.generated_code_mode,
        generated_comment_markers=tuple(args.generated_comment_marker),
        resolve_wildcard_imports=not args.no_resolve_wildcard_imports,
        mark_import_conflicts=not args.no_mark_import_conflicts,
        resolve_method_targets=not args.no_resolve_method_targets,
        resolve_framework_relations=not args.no_resolve_framework_relations,
        embedding_text_mode=args.embedding_text_mode,
        retrieval_output_mode=args.retrieval_output_mode,
        importance_scoring_mode=args.importance_scoring_mode,
    )

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
        include_globs=args.include_glob,
        exclude_globs=args.exclude_glob,
        limits=limits,
        incremental=args.incremental,
        rebuild=args.rebuild,
        cache_file=Path(args.cache_file).resolve(),
        java_extractor_cls=java_extractor_cls,
        adoc_extractor_cls=adoc_extractor_cls,
        xml_extractor_cls=xml_extractor_cls,
        xsd_extractor_cls=xsd_extractor_cls,
    )
