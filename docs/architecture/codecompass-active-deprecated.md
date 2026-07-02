# CodeCompass Active-vs-Deprecated-Erkennung (COSMOS-010)

## Ziel

Kontexttreffer sollen erkennen lassen, ob Code aktiv genutzt, veraltet, tot oder riskant
ist. Status basiert auf messbaren Signalen und Evidence — nicht auf LLM-Schätzungen.

---

## Status-Modell

```python
class CodeStatus(str, Enum):
    ACTIVE          = "active"          # klare Evidence für aktive Nutzung
    LIKELY_ACTIVE   = "likely_active"   # schwache, aber plausible Nutzungszeichen
    UNKNOWN         = "unknown"         # keine ausreichenden Signale
    DEPRECATED      = "deprecated"      # explizit als veraltet markiert
    DEAD_CANDIDATE  = "dead_candidate"  # keine Nutzung nachweisbar, kein aktueller Commit
    RISKY           = "risky"           # aktiv, aber mit bekannten Problemen (Security, TODO)
    UNCERTAIN       = "uncertain"       # widersprüchliche Signale, Einzelnachweise erforderlich
```

---

## Signale und Gewichtungen

| Signal                       | Gewichtung | Erklärung                                                          |
|------------------------------|------------|--------------------------------------------------------------------|
| `call_graph_reachability`    | hoch       | Funktion ist aus einer Entry-Point-Funktion erreichbar → active    |
| `runtime_config_reference`   | hoch       | Config-Key oder Feature-Flag referenziert diesen Code-Pfad         |
| `route_or_endpoint_reference`| hoch       | Code-Pfad ist über eine API-Route erreichbar                       |
| `deprecation_annotations`    | hoch       | `@deprecated`, `# TODO: remove`, `DEPRECATED` im Docstring         |
| `test_coverage_presence`     | mittel     | Mindestens ein Testfall deckt diesen Pfad ab                       |
| `recent_commits`             | mittel     | Commit in den letzten 90 Tagen, der diese Datei berührt           |
| `usage_in_build_or_ci`       | mittel     | Datei wird in Build-Skript, CI-Config oder Makefile referenziert   |
| `docs_mentions`              | niedrig    | Vorkommen in Dokumentation oder README                             |
| `manual_override`            | absolut    | Überschreibt alle anderen Signale; immer auditierbar               |

Gewichtungen beeinflussen den Score, der den Status bestimmt. Kein einzelnes Signal
(außer `manual_override`) erzwingt alleine einen Status.

---

## Konfliktbehandlung

Widersprüchliche Signale (z. B. `call_graph_reachability=true` + `deprecation_annotations=true`)
führen zu `UNCERTAIN`:

```python
@dataclass
class StatusResult:
    status: CodeStatus
    score: float                   # gewichtete Summe der Signale
    signals: list[SignalEvidence]  # alle ausgewerteten Signale mit Einzelergebnis
    conflict_notes: list[str]      # Erklärung bei UNCERTAIN
    manual_override: ManualOverride | None
```

Regel: Wenn `score >= 0.7` und gleichzeitig `deprecation_annotations` aktiv →
Status `UNCERTAIN`, nicht `ACTIVE`. Nur `manual_override` löst den Konflikt.

---

## Manuelle Overrides

```python
@dataclass
class ManualOverride:
    target_node_id: str
    forced_status: CodeStatus
    author: str
    reason: str
    expires_at: datetime | None    # optionales Ablaufdatum
    created_at: datetime
```

Overrides sind:
- In der Datenbank gespeichert, nicht im Quellcode.
- Auditierbar: jede Änderung wird als Event protokolliert.
- Nicht stiller Fallback: abgelaufene Overrides werden als `UNKNOWN` behandelt, nicht stumm ignoriert.

---

## Kontext-Ranking

Deprecated/dead_candidate werden in der Context Curation Pipeline **abgewertet**, nicht entfernt:

| Status           | Ranking-Modifikator | Anzeige im Output               |
|------------------|---------------------|---------------------------------|
| `active`         | +0.2                | —                               |
| `likely_active`  | +0.1                | —                               |
| `unknown`        | 0.0                 | —                               |
| `deprecated`     | -0.3                | Warning: "veraltet"             |
| `dead_candidate` | -0.5                | Warning: "möglicherweise tot"   |
| `risky`          | 0.0                 | Warning: "bekannte Probleme"    |
| `uncertain`      | -0.1                | Warning: "widersprüchliche Signale" |

Entfernt werden Treffer nur, wenn sie im Policy-Filter scheitern (denied_path) oder das
Token-Budget überschritten wird — nicht wegen ihres Status allein.

---

## Tests

| Test                             | Beschreibung                                                         |
|----------------------------------|----------------------------------------------------------------------|
| `test_active_via_call_graph`     | Erreichbare Funktion → status=active                                 |
| `test_deprecated_annotation`     | `@deprecated` im Code → status=deprecated                           |
| `test_conflict_uncertain`        | Erreichbar + deprecated → status=uncertain, signals dokumentiert    |
| `test_dead_candidate_no_signals` | Keine Signale, kein Commit > 90 Tage → status=dead_candidate        |
| `test_manual_override_absolute`  | Override setzt Status unabhängig von Signalen                       |
| `test_override_expiry`           | Abgelaufener Override → zurück zu Signal-basiertem Status           |
| `test_ranking_modifier_applied`  | deprecated-Treffer hat niedrigeren Score in Curation-Output         |
