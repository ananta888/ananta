# Public Verification Bundle

> TRANS-009 | Export | Status: open

## Zweck

Ein Verification Bundle ermöglicht es, einen Ananta-Run so zu exportieren, dass
Dritte ihn prüfen können — ohne Secrets, ohne private Codeinhalte, ohne Zugang
zum originalen Repository.

Das Bundle ist für Open-Source-Projekte, öffentliche Audits, Bug-Reports und
Peer-Review gedacht. Es ersetzt nicht das vollständige Trace-Set, enthält aber
genug Information um zu verifizieren, was passiert ist und ob es den Regeln entsprach.

---

## Bundle-Inhalt

```
verification-bundle-<run_id>/
├── manifest.json          # Bundle-Version, run_id, Zeitstempel, Hashes aller Dateien
├── config_hash.txt        # SHA256 der aktiven Konfiguration zum Run-Zeitpunkt
├── policy_snapshot_summary.json   # PolicySnapshot ohne sensitive Pfad-Werte
├── context_trace_metadata.json    # Treffer-Typen, Scores — kein Snippet-Text
├── tool_call_hashes.jsonl         # SHA256 je Input/Output-Paar pro ToolCall
├── diff_hashes.jsonl              # SHA256 je DiffProposal
└── run_report.md                  # Lesbarer Bericht ohne Secrets
```

### config_hash.txt

SHA256-Hash der gesamten aktiven Konfigurationsdatei zum Startzeitpunkt des Runs.
Ermöglicht Verifikation, dass keine nachträgliche Konfigurationsänderung stattfand.

### policy_snapshot_summary.json

Felder des PolicySnapshot, bei denen sensitive Pfad-Werte durch Platzhalter ersetzt sind:

```json
{
  "run_id": "run-abc123",
  "policy_scope_id": "scope-xyz",
  "allowed_paths": ["<redacted: 3 paths>"],
  "denied_paths": ["<redacted: 5 paths>"],
  "allowed_tools": ["read_file", "search_symbols", "get_file_context"],
  "denied_tools": ["shell_exec", "write_file"],
  "model_policy": "local_only",
  "network_policy": "deny_all",
  "write_policy": "requires_approval",
  "created_at": "2026-07-01T14:22:00Z",
  "config_hash": "sha256:a1b2c3..."
}
```

Pfad-Werte werden ersetzt, weil sie private Projektstrukturen offenlegen könnten.
Die Anzahl der Pfade bleibt sichtbar.

### context_trace_metadata.json

Treffer-Statistiken ohne Snippet-Text:

```json
{
  "run_id": "run-abc123",
  "total_candidates": 42,
  "selected_items": 8,
  "discarded_items": 34,
  "discard_reasons": {
    "denied_path": 12,
    "low_score": 15,
    "over_budget": 7
  },
  "providers_used": ["codecompass"],
  "external_providers": []
}
```

Kein Snippet-Text, keine Dateipfade, keine Funktionsnamen im Standard-Bundle.

### tool_call_hashes.jsonl

Pro Zeile ein ToolCall:

```json
{"tool_call_id": "tc-001", "tool_name": "read_file", "input_hash": "sha256:...", "output_hash": "sha256:...", "policy_decision": "allowed", "status": "ok", "duration_ms": 12}
{"tool_call_id": "tc-002", "tool_name": "shell_exec", "input_hash": "sha256:...", "output_hash": null, "policy_decision": "blocked", "status": "blocked", "duration_ms": 0}
```

Inputs und Outputs sind nur als Hash enthalten. Inhalte werden nie exportiert.

### diff_hashes.jsonl

```json
{"diff_proposal_id": "dp-001", "files_changed": 3, "diff_hash": "sha256:...", "risk_score": 0.2, "approval_required": true, "applied": false}
```

### run_report.md

Lesbarer Bericht mit Zielbeschreibung, Worker-Auswahl, Zusammenfassung der
Tool-Calls, blockierten Aktionen, Approval-Status und offenem Risiko.
Keine Codeschnipsel, keine Pfade, keine Secrets.

---

## Redaktions-Regeln

| Inhaltstyp | Behandlung |
|---|---|
| Dateipfade | Nur Anzahl, kein Pfad-Text |
| Code-Snippets | Nicht exportiert |
| Secrets, Tokens, API-Keys | Nicht exportiert, nicht als Hash |
| ENV-Variablen | Nicht exportiert |
| Modell-Output-Text | Nicht exportiert |
| Hashes von Inputs/Outputs | Exportiert |
| Policy-Felder ohne sensitive Werte | Exportiert |
| Approval-Zeitstempel und -Entität | Exportiert |

**Secrets werden nicht einmal als Hash exportiert.** Ein Hash eines bekannten
Secrets kann zur Verifikation des Geheimnisses missbraucht werden.

---

## Optionaler Full-Export

Mit expliziter Nutzer-Freigabe kann ein Bundle zusätzlich enthalten:

- Vollständige Pfad-Listen aus PolicySnapshot
- Snippet-Text aus ContextTrace
- ToolCall-Inputs und -Outputs im Klartext
- Vollständige DiffProposals

Die Freigabe muss interaktiv bestätigt werden. Sie kann nicht durch Worker,
API-Call oder Konfiguration automatisch aktiviert werden.

Full-Export-Bundles sind klar als `full_export: true` markiert und enthalten
einen Warnhinweis, dass private Inhalte enthalten sind.

---

## Verifikations-Anleitung

Ein Dritter kann ein Standard-Bundle wie folgt prüfen:

1. **Config-Hash**: SHA256 der bekannten Konfigurationsdatei berechnen und mit
   `config_hash.txt` vergleichen.

2. **Policy-Konsistenz**: `policy_snapshot_summary.json` lesen und prüfen, ob
   `model_policy`, `network_policy` und `write_policy` den erwarteten Werten
   entsprechen.

3. **Blockierte Aktionen**: In `tool_call_hashes.jsonl` nach `policy_decision: blocked`
   suchen. Jede blockierte Aktion belegt, dass Default-Deny wirksam war.

4. **Diff-Status**: In `diff_hashes.jsonl` prüfen ob `applied: false` gesetzt ist
   für Proposals, die nicht angewendet werden sollten.

5. **Run Report**: `run_report.md` lesen für die narrative Zusammenfassung.

Für tiefere Prüfung kann ein Full-Export vom Run-Eigentümer angefordert werden.

---

## Hash-Stabilität

Hashes im Verification Bundle sind deterministisch:

- Gleiche Inputs erzeugen gleiche Hashes (keine zufälligen Salts in Tool-Hashes)
- Input-Hashes schließen tool_name, serialisierte Parameter und timestamp_bucket ein
- Timestamp-Bucket: Rundung auf 1-Minuten-Intervall für Reproduzierbarkeit

---

## Tests

| Test | Erwartetes Ergebnis |
|---|---|
| Bundle-Export enthält keine Secrets | Kein Token, Key oder .env-Wert im Bundle |
| Hash bei identischem Run stabil | Gleiche Hashes bei identischen Inputs |
| Pfade in policy_snapshot_summary redaktiert | Nur Anzahl, kein Pfad-Text |
| Snippet-Text nicht im Bundle | context_trace_metadata enthält kein snippet-Feld |
| Full-Export nur nach interaktiver Bestätigung | Kein automatischer Full-Export |
| manifest.json enthält Hashes aller Bundle-Dateien | Integrität des Bundles selbst prüfbar |

---

## Weiterführend

- `docs/architecture/transparency-and-local-safety.md` — Transparenz-Prinzipien
- `docs/architecture/local-only-mode.md` — Lokaler Betrieb
