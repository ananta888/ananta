from __future__ import annotations

import argparse
from pathlib import Path

from rag_helper.application.config_profiles import load_profile_config
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Wert muss > 0 sein")
    return parsed


def resolve_runtime_output_path(value: str | None, out_dir: Path, fallback: Path) -> Path | None:
    if value is None:
        return fallback
    if value == "":
        return None
    resolved_value = value.replace("{out}", str(out_dir))
    return Path(resolved_value).resolve()


def run_cli(
    default_extensions: set[str],
    default_excludes: set[str],
    java_extractor_cls,
    adoc_extractor_cls,
    xml_extractor_cls,
    xsd_extractor_cls,
    text_extractor_cls=None,
) -> None:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", help="JSON- oder YAML-Profil mit CLI-Defaults")
    config_args, _ = config_parser.parse_known_args()
    config_defaults, config_path = load_profile_config(
        Path(config_args.config) if config_args.config else None
    )
    config_default = lambda key, fallback=None: config_defaults.get(key, fallback)

    parser = argparse.ArgumentParser(
        description="Convert Java/XML/XSD/AsciiDoc project files into AST/structure-based RAG JSONL v3."
    )
    parser.add_argument("--config", help="JSON- oder YAML-Profil mit CLI-Defaults")
    parser.add_argument("root", nargs="?", default=config_default("root"), help="Projektverzeichnis")
    parser.add_argument("-o", "--out", default=config_default("out", "rag_out"), help="Ausgabeverzeichnis")
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=config_default("extensions", sorted(default_extensions)),
        help="Dateiendungen ohne Punkt",
    )
    parser.add_argument(
        "--exclude-trivial-methods",
        action="store_true",
        default=config_default("exclude_trivial_methods", False),
        help="Getter/Setter und ähnliche triviale Methoden auslassen",
    )
    parser.add_argument(
        "--no-code-snippets",
        action="store_true",
        default=config_default("no_code_snippets", False),
        help="Keine Code-Snippets in details.jsonl",
    )
    parser.add_argument(
        "--no-xml-node-details",
        action="store_true",
        default=config_default("no_xml_node_details", False),
        help="Keine detaillierten XML-Node-Records erzeugen",
    )
    parser.add_argument(
        "--include-glob",
        action="append",
        default=list(config_default("include_glob", [])),
        help="Nur Dateien verarbeiten, deren relativer Pfad auf dieses Glob-Muster passt",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=list(config_default("exclude_glob", [])),
        help="Dateien mit passendem relativem Pfad-Glob zusätzlich ausschließen",
    )
    parser.add_argument(
        "--max-file-size-kb",
        type=positive_int,
        default=config_default("max_file_size_kb"),
        help="Dateien oberhalb dieser Größe in KB überspringen",
    )
    parser.add_argument(
        "--max-xml-nodes",
        type=positive_int,
        default=config_default("max_xml_nodes"),
        help="XML/XSD-Dateien mit mehr Knoten überspringen",
    )
    parser.add_argument(
        "--max-methods-per-class",
        type=positive_int,
        default=config_default("max_methods_per_class"),
        help="Maximalzahl extrahierter Methoden pro Java-Typ",
    )
    parser.add_argument(
        "--max-records-per-file",
        type=positive_int,
        default=config_default("max_records_per_file"),
        help="Dateien überspringen, wenn mehr Records entstehen würden",
    )
    parser.add_argument(
        "--max-relation-records-per-file",
        type=positive_int,
        default=config_default("max_relation_records_per_file"),
        help="Kappt Relations pro Datei nach Prioritaet statt die ganze Datei zu verlieren",
    )
    parser.add_argument(
        "--max-workers",
        type=positive_int,
        default=config_default("max_workers", 1),
        help="Maximale Zahl parallel verarbeiteter Dateien; 1 bleibt seriell",
    )
    parser.add_argument(
        "--xml-mode",
        choices=("all", "config-only", "smart"),
        default=config_default("xml_mode", "all"),
        help="XML-Verarbeitung: alle Dateien, nur Config/XML oder heuristisch smart",
    )
    parser.add_argument(
        "--xml-index-mode",
        choices=("tags", "summary"),
        default=config_default("xml_index_mode", "tags"),
        help="Schreibt pro XML alle Tag-Records oder nur eine aggregierte Tag-Zusammenfassung",
    )
    parser.add_argument(
        "--xml-relation-mode",
        choices=("per-node", "by-tag", "summary"),
        default=config_default("xml_relation_mode", "per-node"),
        help="XML-Relations granular pro Knoten, kompakt pro Tag oder nur ueber XML-Summaries abbilden",
    )
    parser.add_argument(
        "--xml-repetitive-child-threshold",
        type=positive_int,
        default=config_default("xml_repetitive_child_threshold", 25),
        help="Ab wie vielen gleichartigen Root-Kindern eine XML als repetitive Daten-XML gilt",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        default=config_default("incremental", False),
        help="Nur geänderte Dateien neu parsen und sonst den Cache wiederverwenden",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        default=config_default("rebuild", False),
        help="Incremental-Cache ignorieren und vollständig neu aufbauen",
    )
    parser.add_argument(
        "--cache-file",
        default=config_default("cache_file"),
        help="Pfad zur Cache-Datei für Incremental-Läufe",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=config_default("resume", False),
        help="Nach Abbruch mit vorhandener Cache-Datei weiterlaufen und fertige Dateien wiederverwenden",
    )
    parser.add_argument(
        "--generated-code-mode",
        choices=("off", "mark", "exclude"),
        default=config_default("generated_code_mode", "mark"),
        help="Generierten Code ignorieren, markieren oder komplett ausschließen",
    )
    parser.add_argument(
        "--generated-comment-marker",
        action="append",
        default=list(config_default("generated_comment_marker", [])),
        help="Zusätzlicher Markertext zur Erkennung generierten Codes",
    )
    parser.add_argument(
        "--no-resolve-wildcard-imports",
        action="store_true",
        default=config_default("no_resolve_wildcard_imports", False),
        help="Wildcard-Imports nicht über bekannte Package-Typen auflösen",
    )
    parser.add_argument(
        "--no-mark-import-conflicts",
        action="store_true",
        default=config_default("no_mark_import_conflicts", False),
        help="Mehrdeutige Typauflösungen nicht explizit markieren",
    )
    parser.add_argument(
        "--no-resolve-method-targets",
        action="store_true",
        default=config_default("no_resolve_method_targets", False),
        help="Keine heuristische Zielauflösung für Methodenaufrufe erzeugen",
    )
    parser.add_argument(
        "--no-resolve-framework-relations",
        action="store_true",
        default=config_default("no_resolve_framework_relations", False),
        help="Keine heuristischen Spring- und JPA-Relations aus Annotationen erzeugen",
    )
    parser.add_argument(
        "--embedding-text-mode",
        choices=("verbose", "compact"),
        default=config_default("embedding_text_mode", "verbose"),
        help="Steuert, wie ausführlich embedding_text-Felder erzeugt werden",
    )
    parser.add_argument(
        "--java-detail-mode",
        choices=("full", "compact"),
        default=config_default("java_detail_mode", "full"),
        help="Steuert, ob Java redundante Detail-Records vollstaendig oder kompakt geschrieben werden",
    )
    parser.add_argument(
        "--java-relation-mode",
        choices=("full", "compact"),
        default=config_default("java_relation_mode", "full"),
        help="Steuert, ob Java alle oder nur kompakte Relations erzeugt",
    )
    parser.add_argument(
        "--retrieval-output-mode",
        choices=("legacy", "split", "both"),
        default=config_default("retrieval_output_mode", "legacy"),
        help="Steuert, ob zusätzlich embedding/context JSONL-Dateien erzeugt werden",
    )
    parser.add_argument(
        "--context-output-mode",
        choices=("full", "compact"),
        default=config_default("context_output_mode", "full"),
        help="Steuert, wie umfangreich context.jsonl aus Detail-Records erzeugt wird",
    )
    parser.add_argument(
        "--output-compaction-mode",
        choices=("off", "aggressive", "ultra"),
        default=config_default("output_compaction_mode", "off"),
        help="Filtert Ausgaben fuer stark verdichtete Gemini-orientierte Outputs",
    )
    parser.add_argument(
        "--gem-partition-mode",
        choices=("off", "domain"),
        default=config_default("gem_partition_mode", "off"),
        help="Erzeugt zusaetzliche fachliche Gemini-Pakete",
    )
    parser.add_argument(
        "--manifest-output-mode",
        choices=("full", "compact"),
        default=config_default("manifest_output_mode", "full"),
        help="Steuert, wie ausfuehrlich manifest.json geschrieben wird",
    )
    parser.add_argument(
        "--relation-output-mode",
        choices=("combined", "split", "both"),
        default=config_default("relation_output_mode", "combined"),
        help="Schreibt Relations als eine Datei, gesplittet nach Typ oder beides",
    )
    parser.add_argument(
        "--output-partition-mode",
        choices=("off", "by-kind"),
        default=config_default("output_partition_mode", "off"),
        help="Schreibt zusaetzlich Index-/Detail-Outputs in Unterordnern nach Kind partitioniert",
    )
    parser.add_argument(
        "--importance-scoring-mode",
        choices=("off", "basic"),
        default=config_default("importance_scoring_mode", "basic"),
        help="Steuert, ob Index- und Retrieval-Records einen Importance-Score erhalten",
    )
    parser.add_argument(
        "--graph-export-mode",
        choices=("off", "jsonl", "neo4j"),
        default=config_default("graph_export_mode", "off"),
        help="Erzeugt optionale Graph-Exports als JSONL oder Neo4j-nahe Knoten/Kanten",
    )
    parser.add_argument(
        "--benchmark-mode",
        choices=("off", "basic"),
        default=config_default("benchmark_mode", "off"),
        help="Erfasst Laufzeit- und Output-Statistiken pro Dateityp und Datei",
    )
    parser.add_argument(
        "--duplicate-detection-mode",
        choices=("off", "basic"),
        default=config_default("duplicate_detection_mode", "off"),
        help="Erkennt einfache Duplikat- oder Boilerplate-Kandidaten und schreibt einen Report",
    )
    parser.add_argument(
        "--specialized-chunker-mode",
        choices=("off", "basic"),
        default=config_default("specialized_chunker_mode", "off"),
        help="Erzeugt zusätzliche domänenspezifische Chunks für Spring XML, Maven POM, XSD, AsciiDoc und JPA",
    )
    parser.add_argument(
        "--output-bundle-mode",
        choices=("off", "zip"),
        default=config_default("output_bundle_mode", "off"),
        help="Schreibt optional zusätzlich ein gebündeltes Archiv der erzeugten Outputs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=config_default("dry_run", False),
        help="Berechnet den Lauf vollständig, schreibt aber keine Output- oder Cache-Dateien",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        default=config_default("progress", False),
        help="Zeigt Fortschritt mit Datei, Prozent sowie Skip-/Error-/Cache-Zählern an",
    )
    parser.add_argument(
        "--error-log-file",
        default=config_default("error_log_file"),
        help="Pfad für einen separaten JSONL-Fehlerlog; leer lassen zum Deaktivieren",
    )

    args = parser.parse_args()

    if not args.root:
        source_note = f" oder in {config_path}" if config_path else ""
        raise SystemExit(f"Projektverzeichnis fehlt. Bitte per CLI angeben{source_note}.")

    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve()
    extensions = {x.lower().lstrip(".") for x in args.extensions}
    limits = ProcessingLimits(
        max_file_size_kb=args.max_file_size_kb,
        max_xml_nodes=args.max_xml_nodes,
        max_methods_per_class=args.max_methods_per_class,
        max_records_per_file=args.max_records_per_file,
        max_relation_records_per_file=args.max_relation_records_per_file,
        max_workers=args.max_workers,
        xml_mode=args.xml_mode,
        xml_index_mode=args.xml_index_mode,
        xml_relation_mode=args.xml_relation_mode,
        xml_repetitive_child_threshold=args.xml_repetitive_child_threshold,
        java_detail_mode=args.java_detail_mode,
        java_relation_mode=args.java_relation_mode,
        generated_code_mode=args.generated_code_mode,
        generated_comment_markers=tuple(args.generated_comment_marker),
        resolve_wildcard_imports=not args.no_resolve_wildcard_imports,
        mark_import_conflicts=not args.no_mark_import_conflicts,
        resolve_method_targets=not args.no_resolve_method_targets,
        resolve_framework_relations=not args.no_resolve_framework_relations,
        embedding_text_mode=args.embedding_text_mode,
        retrieval_output_mode=args.retrieval_output_mode,
        context_output_mode=args.context_output_mode,
        output_compaction_mode=args.output_compaction_mode,
        gem_partition_mode=args.gem_partition_mode,
        manifest_output_mode=args.manifest_output_mode,
        relation_output_mode=args.relation_output_mode,
        output_partition_mode=args.output_partition_mode,
        importance_scoring_mode=args.importance_scoring_mode,
        graph_export_mode=args.graph_export_mode,
        benchmark_mode=args.benchmark_mode,
        duplicate_detection_mode=args.duplicate_detection_mode,
        specialized_chunker_mode=args.specialized_chunker_mode,
        output_bundle_mode=args.output_bundle_mode,
    )

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Ungültiges Verzeichnis: {root}")

    cache_file = resolve_runtime_output_path(
        args.cache_file,
        out_dir,
        out_dir / ".cache" / "code_to_rag_cache.json",
    )
    error_log_file = resolve_runtime_output_path(
        args.error_log_file,
        out_dir,
        out_dir / ".errors" / "errors.jsonl",
    )

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
        resume=args.resume,
        cache_file=cache_file,
        java_extractor_cls=java_extractor_cls,
        adoc_extractor_cls=adoc_extractor_cls,
        xml_extractor_cls=xml_extractor_cls,
        xsd_extractor_cls=xsd_extractor_cls,
        text_extractor_cls=text_extractor_cls,
        dry_run=args.dry_run,
        show_progress=args.progress,
        error_log_file=error_log_file,
    )
