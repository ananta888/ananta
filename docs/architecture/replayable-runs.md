# Replayable Runs
<!-- COSMOS-007 -->

## Zweck

Jeder abgeschlossene Run kann später inspiziert, nachvollzogen und — unter expliziter
Freigabe — erneut ausgeführt werden. Replay ist kein automatischer Mechanismus, sondern
ein auditiertes, opt-in-Werkzeug für Debugging, Compliance und Qualitätssicherung.

---

## ReplayRecord Schema

```python
@dataclass
class ReplayRecord:
    replay_id: str              # uuid, unveränderlich
    run_id: str                 # Referenz auf den originalen Run

    expert_id: str
    expert_version: str         # exakte Version, nicht "latest"
    config_hash: str            # sha256 der aktiven Konfiguration zum Run-Zeitpunkt
    policy_snapshot_ref: str    # artifact_id des PolicySnapshot-Artefakts

    context_bundle_refs: list[str]   # artifact_ids aller Context-Bundles des Runs
    tool_call_log_ref: str           # artifact_id des vollständigen Tool-Call-Logs
    non_deterministic_refs: list[str]  # artifact_ids externer Antwort-Snapshots

    created_at: float
    replay_feasibility: str     # "full" | "partial" | "analyse_only" | "not_possible"
    missing_snapshots: list[str]  # Gründe, falls nicht vollständig replay-fähig
```

---

## Replay-Modi

| Modus            | Beschreibung                                                       | Approval nötig |
|------------------|--------------------------------------------------------------------|----------------|
| analyse_replay   | Read-only: zeigt was passiert ist, Artefakte und Entscheidungen    | nein           |
| action_replay    | Führt Schritte erneut aus mit gespeichertem Kontext                | ja, separat    |

`action_replay` braucht eigene ApprovalRequests — er erbt nicht die Approvals des
Original-Runs. Jeder schreibende Schritt muss neu freigegeben werden.

---

## Nicht-Determinismus

LLM-Antworten und externe API-Calls sind nicht deterministisch. Replay-Strategie:

| Typ                          | Strategie                                              |
|------------------------------|--------------------------------------------------------|
| LLM-Antwort (lokal)          | Snapshot als `non_deterministic_refs`-Artefakt         |
| LLM-Antwort (Cloud)          | Snapshot oder `replay_feasibility: "not_possible"`     |
| Externe API-Antwort          | Snapshot mit Timestamp                                 |
| Dateiinhalt zum Run-Zeitpunkt| `input_snapshot`-Artefakt (bereits im Artifact Model)  |
| Aktuelle Systemzeit          | Im Tool-Call-Log mit Timestamp gespeichert             |

Wenn ein Snapshot fehlt, wird `replay_feasibility` auf `"partial"` oder `"not_possible"`
gesetzt. UI/CLI zeigt dies vor einem Replay-Versuch an.

---

## Dry-Run

```bash
ananta replay --run <run_id> --dry-run
```

`--dry-run` verhindert alle schreibenden Side Effects:
- Kein Datei-Schreiben
- Kein Netzwerkzugriff
- Kein ApprovalRequest wird erstellt
- Sandbox läuft in read-only-Modus

Dry-Run-Ergebnis wird als separates Artefakt gespeichert (`artifact_type: "final_summary"`,
`created_by: "replay_dry_run"`).

---

## PolicySnapshot

Ein `PolicySnapshot` ist ein unveränderliches Abbild der aktiven Policy zum Run-Zeitpunkt.
Er wird als `internal`-Artefakt am Run-Start erstellt und ist Bestandteil des ReplayRecords.

Ohne PolicySnapshot ist `action_replay` nicht möglich — nur `analyse_replay`.

---

## Analyse-Werkzeuge (UI/CLI)

```bash
ananta replay --run <run_id> --analyse
```

Ausgabe:
- Run-Metadaten und Expert-Version
- Replay-Feasibility und fehlende Snapshots
- Chronologische Tool-Call-Liste mit Inputs/Outputs
- Artefakt-Liste mit Policy-Klassen
- ApprovalRecords und Gate-Entscheidungen

```bash
ananta replay --run <run_id> --check-prerequisites
```

Gibt JSON zurück:
```json
{
  "replay_feasibility": "partial",
  "missing_snapshots": ["llm_response:step_3"],
  "incompatible_expert_version": false,
  "policy_snapshot_available": true
}
```

---

## Voraussetzungen für action_replay

1. `replay_feasibility` ist `"full"` oder `"partial"` (mit expliziter Bestätigung)
2. Expert-Version ist noch im System registriert
3. PolicySnapshot vorhanden
4. Operator hat `action_replay` für den spezifischen Run freigegeben
5. `--dry-run` wurde vor dem echten Replay ausgeführt (empfohlen, nicht erzwungen)

---

## Tests

| Testfall                                           | Erwartung                                             |
|----------------------------------------------------|-------------------------------------------------------|
| ReplayRecord mit allen Pflichtfeldern              | Valide, gespeichert                                   |
| ReplayRecord ohne policy_snapshot_ref              | ValidationError                                       |
| analyse_replay ohne Approval                       | Erfolg, read-only                                     |
| action_replay ohne Approval                        | ApprovalRequest erstellt, kein Start                  |
| dry_run schreibt keine Dateien                     | Alle schreibenden Calls werden als Stubs behandelt    |
| fehlender LLM-Snapshot für Schritt 3               | replay_feasibility="partial", Warnung in CLI          |
| Replay mit inkompatiblem expert_version            | Warnung und Abbruch (kein stiller Fallback)           |
| Dry-Run-Ergebnis als Artefakt                      | final_summary mit created_by="replay_dry_run"         |
