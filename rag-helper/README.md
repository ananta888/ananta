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
- Python- und TypeScript-Symbol-Outlines

### Verarbeitung / Betrieb
- Include-/Exclude-Glob-Filter
- Größenlimits für Dateien, XML-Knoten und Records
- Incremental Cache, Resume-Modus und Parallelisierung
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
- `--incremental`
- `--rebuild`
- `--resume`
- `--max-workers`
- `--progress`
- `--dry-run`
- `--error-log-file`
- `--retrieval-output-mode legacy|split|both`
- `--graph-export-mode off|jsonl|neo4j`
- `--benchmark-mode off|basic`
- `--duplicate-detection-mode off|basic`
- `--specialized-chunker-mode off|basic`

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
    "max_xml_nodes": 5000
  },
  "modes": {
    "xml_mode": "smart",
    "embedding_text_mode": "compact",
    "retrieval_output_mode": "both",
    "graph_export_mode": "neo4j",
    "benchmark_mode": "basic"
  },
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
