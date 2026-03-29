# codecompass_rag.py

Struktur-basierter Konverter für Code- und Konfigurations-Repositories zu RAG-freundlichen JSONL-Dateien.

Das Tool extrahiert nicht nur Rohtext, sondern auch Typen, Methoden, Beziehungen, spezialisierte Chunks und optionale Zusatz-Outputs für Retrieval, Graph und Betriebsanalyse.

## Unterstützte Dateitypen

- `java`
- `xml`
- `xsd`
- `adoc`
- `properties`
- `yaml`
- `yml`
- `sql`
- `md`
- `py`
- `ts`
- `tsx`

## Wichtige Features

### Java
- Package-, Import- und Typ-Erkennung
- Klassen, Interfaces, Enums, Records, Annotation Types
- Felder, Methoden, Konstruktoren
- Typauflösung inklusive Wildcard-Imports und Konfliktmarkierung
- leichte Method-Target-Heuristik
- Spring-/JPA-Relations
- Rollenklassifikation, JPA-Entity-Chunks und Parent/Child-Links

### XML / XSD / ADOC
- Smart XML Filtering für Config- vs. Daten-XML
- Spring-XML-Bean-Chunks
- Maven-POM-Chunks inklusive Dependency-Zusammenfassung
- XSD-Typen, Root-Elemente und XSD-Schema-Chunks
- AsciiDoc-Sektionen, Codeblöcke und Architektur-Chunks

### Weitere Textdateien
- Markdown-Sektionen
- YAML-/Properties-Entries
- SQL-Statements
- Python-Module mit Imports, Klassen, Methoden, Decorators und Funktionen
- TypeScript-/TSX-Dateien mit Imports, Klassen, Methoden, Decorators und Extends-/Implements-Hinweisen

### Verarbeitung / Betrieb
- Include-/Exclude-Glob-Filter
- Größenlimits für Dateien, XML-Knoten und Records
- Incremental Cache mit Shards pro Dateityp, Resume-Modus und Parallelisierung
- Fortschrittsanzeige
- separater Fehler-Log
- Dry-Run ohne Datei-Outputs
- Benchmark-Report
- Duplicate-/Boilerplate-Erkennung

## Outputs

Standard:

- `index.jsonl`
- `details.jsonl`
- `relations.jsonl`
- `manifest.json`

Optional je nach Modus:

- `embedding.jsonl`
- `context.jsonl`
- `graph_nodes.jsonl`
- `graph_edges.jsonl`
- `benchmark.json`
- `duplicates.json`
- `errors.jsonl`
- `output_bundle.zip`

## Wichtige CLI-Optionen

Beispiele:

```bash
python3 codecompass_rag.py /repo -o rag_out
python3 codecompass_rag.py /repo --include-glob "src/**/*.java" --exclude-glob "target/**"
python3 codecompass_rag.py /repo --incremental --resume --cache-file .code_to_rag_cache.json
python3 codecompass_rag.py /repo --retrieval-output-mode both --graph-export-mode neo4j
python3 codecompass_rag.py /repo --benchmark-mode basic --duplicate-detection-mode basic
python3 codecompass_rag.py /repo --specialized-chunker-mode basic --progress
python3 codecompass_rag.py /repo --dry-run
python3 codecompass_rag.py --config examples/rag-profile.json
```

Besonders relevante Schalter:

- `--config`
- `--include-glob` / `--exclude-glob`
- `--max-file-size-kb`
- `--max-xml-nodes`
- `--max-methods-per-class`
- `--max-records-per-file`
- `--max-relation-records-per-file`
- `--incremental`
- `--rebuild`
- `--resume`
- `--max-workers`
- `--java-relation-mode full|compact`
- `--java-detail-mode full|compact`
- `--xml-index-mode tags|summary`
- `--xml-relation-mode per-node|by-tag|summary`
- `--context-output-mode full|compact`
- `--output-compaction-mode off|aggressive`
- `--output-compaction-mode off|aggressive|ultra`
- `--gem-partition-mode off|domain`
- `--manifest-output-mode full|compact`
- `--relation-output-mode combined|split|both`
- `--output-partition-mode off|by-kind`
- `--progress`
- `--dry-run`
- `--error-log-file`
- `--retrieval-output-mode legacy|split|both`
- `--graph-export-mode off|jsonl|neo4j`
- `--benchmark-mode off|basic`
- `--duplicate-detection-mode off|basic`
- `--specialized-chunker-mode off|basic`
- `--output-bundle-mode off|zip`

## Profil-Konfiguration

JSON und YAML werden unterstützt. Relative Pfade in Profilen werden relativ zur Konfigurationsdatei aufgelöst.

Beispiel:

```json
{
  "root": "../project",
  "out": "../rag_out",
  "extensions": ["java", "xml", "xsd", "adoc", "py", "ts"],
  "filters": {
    "include_glob": ["src/**"],
    "exclude_glob": ["target/**", "build/**"]
  },
  "limits": {
    "max_workers": 4,
    "max_records_per_file": 500,
    "max_relation_records_per_file": 80,
    "max_xml_nodes": 5000
  },
  "modes": {
    "xml_mode": "smart",
    "xml_index_mode": "summary",
    "xml_relation_mode": "summary",
    "embedding_text_mode": "compact",
    "java_detail_mode": "compact",
    "java_relation_mode": "compact",
    "retrieval_output_mode": "both",
    "context_output_mode": "compact",
    "output_compaction_mode": "aggressive",
    "gem_partition_mode": "domain",
    "manifest_output_mode": "compact",
    "relation_output_mode": "split",
    "output_partition_mode": "by-kind",
    "graph_export_mode": "neo4j",
    "benchmark_mode": "basic"
  },
  "exclude_trivial_methods": true,
  "no_code_snippets": true,
  "no_xml_node_details": true,
  "flags": {
    "incremental": true,
    "resume": true,
    "progress": true
  }
}
```

## Installation

```bash
python3 -m pip install -r requirements.txt
```

## Tests

Komplette lokale Checks:

```bash
python3 scripts/run_ci_checks.py
```

Einzelsuiten:

```bash
python3 -m unittest tests.test_cli_config
python3 -m unittest tests.test_processing_limits
python3 -m unittest tests.test_post_processing_features
```

## Empfehlung fuer grosse Spring-/XML-Projekte

Fuer sehr grosse Repositories und Gemini-Gems-Workflows sind kompakte Modi deutlich sinnvoller als Vollausgaben:

```bash
python3 codecompass_rag.py . \
  -o ./rag_out \
  --config spring-large-project-profile-no-resume.json
```

Empfohlene Stellschrauben:

- `no_code_snippets: true`
- `no_xml_node_details: true`
- `xml_index_mode: "summary"`
- `xml_relation_mode: "summary"`
- `java_detail_mode: "compact"`
- `java_relation_mode: "compact"`
- `context_output_mode: "compact"`
- `output_compaction_mode: "aggressive"`
- `gem_partition_mode: "domain"`
- `manifest_output_mode: "compact"`
- `relation_output_mode: "split"`
- Tests (`src/test/**`, `**/*Test.java`, `**/*IT.java`) fuer Gemini-orientierte Laeufe eher ausschliessen
- `output_partition_mode: "by-kind"`
- `max_relation_records_per_file`

Damit bleiben `manifest.json`, `embedding.jsonl` und `context.jsonl` fuer Retrieval nutzbar, ohne dass `relations.jsonl` in den Multi-GB-Bereich waechst. Zusaetzlich werden bei Bedarf `relations_by_type/`, `index_by_kind/` und `details_by_kind/` erzeugt.

Cache und Fehlerlogs landen in den grossen Profilen standardmaessig direkt unter dem Output-Verzeichnis:

- `rag_out/.cache/code_to_rag_cache.json`
- `rag_out/.cache/code_to_rag_cache.json.d/` fuer die eigentlichen Cache-Shards pro Dateityp
- `rag_out/.errors/errors.jsonl`

Die sichtbare Cache-JSON ist deshalb absichtlich klein und enthaelt nur die Shard-Steuerinformationen.

Wenn maximale Verkleinerung wichtiger ist als Legacy-Exports, gibt es zusaetzlich ein Ultra-Profil:

```bash
python3 codecompass_rag.py . \
  -o ./rag_out_ultra \
  --config spring-large-project-profile-ultra-no-resume.json
```

`output_compaction_mode: "ultra"` schreibt nur noch die kleinsten Gemini-orientierten Artefakte:

- `manifest.json`
- `gems_by_domain/*.jsonl`
- optional `output_bundle.zip`
- `.cache/` und `.errors/`

Die grossen Legacy-Dateien wie `index.jsonl`, `details.jsonl`, `relations_by_type/`, `index_by_kind/`, `details_by_kind/`, `embedding.jsonl` und `context.jsonl` werden in diesem Modus nicht mehr geschrieben.

## Projektstruktur

- `codecompass_rag.py`
  CLI-Einstieg und Java-Hauptpfad
- `rag_helper/application/`
  Projektverarbeitung, Reports, Nachbearbeitung und Konfigurationsprofile
- `rag_helper/extractors/`
  Dateityp-spezifische Extraktoren
- `rag_helper/utils/`
  IDs, Embedding-Text und Textnormalisierung
- `tests/`
  Regressionstests für CLI, Limits, Exporte und Post-Processing
