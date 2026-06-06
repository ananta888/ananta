# CodeCompass Relevant-Snippet Handoff

## Überblick

Dieser Artikel beschreibt, wie Ananta CodeCompass-Kontext aus `rag_helper/research-context.json` an den ananta-worker Prompt übergibt — vom aktuellen Pfad-only-Fallback bis zum erweiterten Snippet-/Line-Range-Handoff.

Implementiert durch `agent/common/sgpt.py::_load_source_file_batches` und `_build_iteration_prompt`.

---

## Aktueller Ist-Vertrag (Pfad-only)

`rag_helper/research-context.json` enthält ein `repo_scope_refs`-Array. Minimalformat:

```json
{
  "repo_scope_refs": [
    { "path": "agent/common/sgpt.py" },
    { "path": "agent/services/knowledge_index_retrieval_service.py" }
  ]
}
```

`_load_source_file_batches` löst jeden `path`-Eintrag gegen `settings.rag_repo_root` auf und liest `raw[:per_file_chars]` (Dateianfang). Pfade außerhalb von `rag_repo_root` werden durch `relative_to`-Prüfung blockiert.

---

## Erweiterter Zielvertrag (Snippet-/Line-Range)

Jeder Eintrag in `repo_scope_refs` darf zusätzliche Felder enthalten:

```json
{
  "path": "agent/common/sgpt.py",
  "ref": "HEAD",
  "symbol": "_load_source_file_batches",
  "start_line": 573,
  "end_line": 648,
  "score": 0.91,
  "reason": "Loads CodeCompass repo_scope_refs and assembles ananta-worker context batches.",
  "snippet": "def _load_source_file_batches(...):\n    ...",
  "metadata": {
    "source_scope": "repo_path",
    "chunk_id": "agent/common/sgpt.py:573-648",
    "retrieval_query": "CodeCompass relevant file batches for ananta-worker"
  }
}
```

**Akzeptierte Alias-Namen:**

| Kanonisch   | Auch akzeptiert                              |
|-------------|----------------------------------------------|
| `start_line`| `line_start`, `start`, `from_line`           |
| `end_line`  | `line_end`, `end`, `to_line`                 |
| `snippet`   | `content`, `excerpt`                         |

Alte `research-context.json`-Dateien ohne Line-Ranges bleiben vollständig gültig.

---

## Prioritätsreihenfolge der Kontextquellen

Pro `repo_scope_refs`-Eintrag gilt (höchste Priorität zuerst):

1. **`path` + `start_line`/`end_line`** → Dateiausschnitt aus `rag_repo_root` lesen, mit konfigurierbarem Kontextfenster (`context_lines`). Snippet dient als Fallback falls Dateilesen scheitert.
2. **`chunks[]` im ref** → Jeder Chunk-Eintrag wird als eigener Kontextblock eingebunden.
3. **`path` only** → Dateianfang bis `per_file_chars` (Fallback-Verhalten wie zuvor).
4. **`snippet` ohne gültigen `path`** → Direkt einbinden, begrenzt auf `max_snippet_chars`.
5. **`.ananta/hub-context.md`** → Nur wenn keine `repo_scope_refs` verwertbar sind.

---

## Prompt-Format

Jeder Kontextblock im Prompt hat einen annotierten Header:

```
### agent/common/sgpt.py:573-648 [line_range score=0.91]
```python
def _load_source_file_batches(...):
    ...
` ``

### agent/common/sgpt.py [file_excerpt]
` ``python
# Dateianfang...
` ``
```

---

## Konfigurierbare Parameter

Alle Grenzen können per Umgebungsvariable oder `AGENT_CONFIG.ananta_worker_context_*` überschrieben werden:

| Einstellung                              | Default | Bedeutung                                    |
|------------------------------------------|---------|----------------------------------------------|
| `ANANTA_WORKER_CONTEXT_FILES_PER_BATCH`  | 3       | Dateien/Chunks pro Iterations-Batch          |
| `ANANTA_WORKER_CONTEXT_PER_FILE_CHARS`   | 4000    | Max. Zeichen pro Kontextblock                |
| `ANANTA_WORKER_CONTEXT_MAX_ITERATIONS`   | 8       | Max. Iterations-Batches pro Lauf             |
| `ANANTA_WORKER_CONTEXT_LINE_WINDOW`      | 5       | Kontext-Zeilen vor/nach Line-Range           |
| `ANANTA_WORKER_CONTEXT_MAX_SNIPPET_CHARS`| 8000    | Max. Zeichen für direkte Snippets            |

---

## Datenfluss

```
CodeCompass/RAG
    ↓
rag_helper/research-context.json  (repo_scope_refs mit path/start_line/end_line/snippet)
    ↓
agent/common/sgpt.py::_load_source_file_batches()
    → Line-Range aus Datei lesen  ← bevorzugt
    → Chunks aus ref.chunks[]     ← alternativ
    → Dateianfang lesen           ← Fallback
    → Snippet direkt einbinden    ← letzter Ausweg
    ↓
_build_iteration_prompt()  (annotierter Header: path:start-end [source_kind score=X])
    ↓
ananta-worker sgpt-Aufruf
    ↓
rag_helper/progress.md  (enthält verarbeitete Quellenangaben)
    ↓
Synthesis → finales Ergebnis
```

---

## Debug-Stellen

- **`rag_helper/research-context.json`** — Welche Refs und Metadaten liefert CodeCompass?
- **`rag_helper/progress.md`** — Welche Dateien/Abschnitte wurden pro Schritt verarbeitet?
- **`.ananta/hub-context.md`** — Fallback-Kontext, wenn research-context.json fehlt oder leer ist.

### Relevanter Abschnitt fehlt?

1. `research-context.json` prüfen: Enthält der ref `start_line`/`end_line`?
2. Wenn nicht: CodeCompass liefert nur `path` → Dateianfang wird geladen. CodeCompass-Index neu aufbauen oder ref manuell mit Line-Range ergänzen.
3. `progress.md` prüfen: Steht `[line_range]` oder `[file_excerpt]` im Schritt-Header?
4. Wenn `[file_excerpt]` obwohl Line-Range erwartet: `settings.rag_repo_root` korrekt konfiguriert? Datei im Container erreichbar?

---

## Sicherheitsgrenzen

- Alle `path`-Auflösungen werden gegen `settings.rag_repo_root` mit `relative_to` geprüft — absolute Pfade und `../` werden blockiert.
- Line-Ranges werden auf `_MAX_LINE_SPAN = 5000` begrenzt.
- Snippet-Direkteinbindung auf `max_snippet_chars` begrenzt.
