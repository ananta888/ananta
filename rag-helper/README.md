# code_to_rag_ast_v3.py

AST-/Struktur-basierter Konverter für große Codebasen zu RAG-freundlichen JSONL-Dateien.

Der Fokus liegt auf:

- Java-Struktur statt reinem Rohtext
- XSD-/XML-Struktur
- kompakter, semantischer Output für Embeddings und Retrieval
- zusätzliche Beziehungsdaten über `relations.jsonl`

Aktuell unterstützt:

- `*.java`
- `*.xml`
- `*.xsd`

---

## Ziel

Das Script bereitet Quellcode und strukturierte Dateien so auf, dass sie besser für RAG-Systeme mit Gemini, ChatGPT oder Vektor-Datenbanken nutzbar sind.

Statt nur Dateien als Fließtext zu komprimieren, extrahiert das Tool:

- Dateistruktur
- Typen
- Methoden
- Konstruktoren
- Felder
- einfache Typbeziehungen
- XML-/XSD-Struktur
- Relationen zwischen Elementen

---

## Features

### Java
- Package- und Import-Erkennung
- Klassen / Interfaces / Enums / Records
- Felder
- Methoden
- Konstruktoren
- Annotationen
- `extends` / `implements`
- einfache Typauflösung
- Getter/Setter-Erkennung
- Rollen-Heuristiken:
  - `service`
  - `controller`
  - `repository`
  - `entity`
  - `dto`
  - `lombok`
  - `record_like`

### XML
- Root-Element
- Namespaces
- Tag-Struktur
- optionale Node-Details
- Child-Tag-Beziehungen

### XSD
- `complexType`
- `simpleType`
- Root-Elemente
- Attribute
- Elemente
- `extension` / Vererbungen

### RAG
- `embedding_text` pro wichtigem Record
- kompakter Index
- Detaildaten getrennt
- Relationen in separater Datei

---

## Ausgabe

Das Script erzeugt im Zielverzeichnis:

### `index.jsonl`
Kompakte Records für Retrieval / Embeddings.

Typische Record-Arten:

- `java_file`
- `java_type`
- `xml_file`
- `xml_tag`
- `xsd_file`
- `xsd_complex_type`
- `xsd_simple_type`
- `xsd_root_element`

### `details.jsonl`
Detailinformationen für Nachladen / erweiterten Kontext.

Typische Record-Arten:

- `java_method`
- `java_method_detail`
- `java_constructor`
- `java_constructor_detail`
- `xml_node_detail`
- `xsd_complex_type_detail`

### `relations.jsonl`
Beziehungen zwischen Typen, Methoden und XSD/XML-Strukturen.

Typische Relationen:

- `extends`
- `implements`
- `field_type_uses`
- `declares_method`
- `declares_constructor`
- `uses_type`
- `returns`
- `calls`
- `contains_child_tag`
- `contains_element_type`
- `contains_element_ref`
- `has_attribute_type`
- `restricted_by`

### `manifest.json`
Zusammenfassung des Laufs:

- Projektpfad
- Anzahl Dateien
- Record-Zahlen
- Optionen
- Package-/Typindex
- Fehler pro Datei

---

## Installation

Benötigte Python-Pakete:

```bash
pip install tree_sitter tree_sitter_java lxml
