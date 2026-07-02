# CodeCompass IDE/Arbeitskontext als optionales Signal (COSMOS-012)

## Ziel

Aktuelle geöffnete Dateien, Cursor-Position, aktive Branch und lokale Änderungen können
das Kontext-Ranking verbessern. Der Arbeitskontext erweitert niemals Zugriffsrechte —
er verändert ausschließlich Ranking-Gewichte.

---

## WorkContext-Port

```python
from typing import Protocol

class WorkContext(Protocol):
    def get_open_files(self) -> list[str]: ...
    def get_active_file(self) -> str | None: ...
    def get_selection(self) -> FileRange | None: ...
    def get_active_branch(self) -> str | None: ...
    def get_dirty_files(self) -> list[str]: ...

@dataclass
class FileRange:
    path: str
    start_line: int
    end_line: int
```

Implementierungen:
- `NullWorkContext` — inaktiv (Default); gibt überall `None` / leere Listen zurück
- `LSPWorkContext` — liest Kontext aus einem LSP-kompatiblen Editor (VSCode, Neovim)
- `CLIWorkContext` — liest `git status` und aktive Branch für CLI-Nutzung
- `FakeWorkContext` — für Tests mit fest verdrahteten Dateipfaden

---

## Verwendung im Ranking

WorkContext beeinflusst **ausschließlich** den Ranking-Score in der Context Curation Pipeline.
Er verändert nicht `allowed_paths`, nicht Policy-Scopes und nicht die Menge der
Kandidaten vor dem Policy-Filter.

Ranking-Modifikatoren:

| Signal                        | Score-Modifikator | Bedingung                                        |
|-------------------------------|-------------------|--------------------------------------------------|
| Treffer in `get_active_file`  | +0.3              | Pfad stimmt exakt überein                        |
| Treffer in `get_open_files`   | +0.15             | Pfad ist in Liste geöffneter Dateien             |
| Treffer liegt in `get_selection` | +0.4           | Zeile liegt im selektierten Bereich              |
| Treffer liegt auf `get_active_branch` | +0.05   | Branch-Name stimmt überein (schwaches Signal)    |
| Treffer ist in `get_dirty_files` | +0.1           | Lokale, ungespeicherte Änderung vorhanden        |

Die Modifikatoren sind additiv und konfigurierbar.

---

## Sicherheitsregel: Keine Rechteerweiterung

WorkContext **erweitert niemals** `allowed_paths` oder Policy-Scopes.

Konkret:
- Eine Datei, die in `get_open_files` auftaucht, aber nicht in `allowed_paths` liegt,
  bleibt gesperrt. WorkContext kann diesen Sperrung nicht aufheben.
- Der Policy-Filter läuft vor dem Ranking — WorkContext greift erst danach.
- WorkContext-Daten werden nicht an externe Provider weitergegeben.

```
retrieve_candidates
  → apply_policy_filter   ← WorkContext hat hier keinen Einfluss
  → deduplicate
  → rank_by_relevance
  → rank_by_freshness
  → rank_by_active_status
  → apply_work_context_boost   ← WorkContext greift nur hier
  → compress_snippets
  → ...
```

---

## Sensitive lokale Änderungen (Dirty Files)

Dirty Files, die Secret-Patterns enthalten, werden redigiert oder blockiert,
bevor sie als Kontext weitergegeben werden:

```python
SENSITIVE_PATTERNS = [
    r"(?i)(api_key|secret|password|token)\s*=\s*\S+",
    r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
    r"AKIA[0-9A-Z]{16}",   # AWS Access Key
]
```

Regeln:
- Dirty File mit Match → Datei wird aus `get_dirty_files` entfernt, Trace-Eintrag mit `reason_code: sensitive_content`.
- Inhalt der Dirty File wird **nicht** in den Kontext aufgenommen, auch wenn die Datei in `allowed_paths` liegt.
- Kein stiller Drop: Trace zeigt `[REDACTED: sensitive_content]` für betroffene Dateien.

---

## Default und Aktivierung

```yaml
# codecompass/config/work_context.yaml
work_context:
  enabled: false               # Default: inaktiv
  provider: null               # "null" | "lsp" | "cli"
  show_in_output: true         # UI/CLI zeigt wenn WorkContext verwendet wurde
  sensitive_pattern_check: true
```

Aktivierung erfordert explizite Konfiguration. Keine automatische Editor-Erkennung.

---

## Anzeige im Output

Wenn WorkContext aktiv ist und Ranking-Gewichte beeinflusst hat, wird dies im Output
sichtbar gemacht:

```
[Kontext] Arbeitskontext aktiv: 3 Treffer durch geöffnete Dateien höher bewertet.
[Kontext] Datei src/auth.py (aktiv im Editor) erhielt +0.3 Ranking-Bonus.
```

Wenn WorkContext inaktiv ist: keine Erwähnung.

---

## Tests

| Test                                | Beschreibung                                                        |
|-------------------------------------|---------------------------------------------------------------------|
| `test_active_file_ranking_boost`    | Treffer in active_file erhält höheren Score als nicht-aktive Datei |
| `test_no_access_extension`          | Datei in open_files aber nicht allowed_paths bleibt gesperrt       |
| `test_policy_filter_before_ranking` | Policy-Filter entfernt Treffer bevor WorkContext-Boost greift      |
| `test_sensitive_dirty_file_redacted`| Dirty File mit API-Key-Pattern → nicht in Kontext, Trace-Eintrag  |
| `test_disabled_work_context`        | enabled=false → NullWorkContext, keine Ranking-Änderung            |
| `test_output_shows_context_used`    | Bei aktivem WorkContext erscheint Hinweis im Output                |
