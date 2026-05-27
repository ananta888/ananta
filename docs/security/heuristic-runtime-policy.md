# Heuristic Runtime Policy

**Track:** heuristic-runtime-registry-snake-chat-ai-evolution-master  
**Task:** T01.01  
**Verbindlich für:** TUI Snake, Eclipse Snake, Chat CodeCompass

---

## 1. Grundprinzip

Aktive Heuristiken laufen **lokal und deterministisch** — ohne LLM-Aufruf pro Tick.  
Ein Worker darf eine Heuristik *auswählen*, aber niemals direkt Heuristik-Code ausführen oder eine neue Heuristik aktivieren.

---

## 2. Rollen

| Rolle | Erlaubt | Verboten |
|---|---|---|
| **ananta-worker** | Heuristik aus Registry auswählen, TTL-Reevaluation anstoßen, Trace auswerten, Candidate Proposals erzeugen | Runtime-Code ausführen, Heuristik direkt aktivieren |
| **opencode** | Genehmigte Codeänderungen implementieren (neues HeuristicDefinition JSON, Tests) | Heuristik-Lease entscheiden, Runtime-Entscheider sein |
| **lokale Runtime** | Aktive Heuristik deterministisch ausführen | LLM pro Tick aufrufen, Capabilities überschreiten |

---

## 3. Fallback-Trigger

Die lokale Heuristik übernimmt automatisch wenn:

| Trigger | Bedingung |
|---|---|
| `ai_timeout` | Worker-Antwort bleibt > 2,5 s aus (Limit in `ai_snake_worker_client.py`) |
| `ai_offline` | Hub nicht erreichbar |
| `invalid_response` | Worker-Antwort verletzt Schema oder Registry-Constraint |
| `low_quality` | Confidence < konfiguriertes Minimum |
| `policy_denied` | `WorkerRoleConfig` blockiert angeforderte Aktion |
| `lease_expired` | TTL der aktiven `HeuristicDecisionLease` abgelaufen |

Im Fallback wird **immer** der `fallback_reason` im `DecisionTrace` eingetragen.

---

## 4. Aktivierungsregeln (No Auto-Activation)

Eine Heuristik wird **niemals automatisch aktiviert**. Jede Aktivierung erfordert alle drei Bedingungen:

1. **Schema-valide** — `HeuristicProposalValidator` bestanden
2. **Simulation-Pass** — `HeuristicSimulationHarness` ohne `policy_violations`
3. **Human Approval** — Audit-Event `heuristic_proposal_approved` vorhanden

Technische Durchsetzung: `HeuristicActivationGate` prüft Audit-Log vor jeder Aktivierung.

---

## 5. Capability-Grenzen

Jede Heuristik deklariert ihre erlaubten Capabilities in `HeuristicDefinition.capabilities`.  
Die Runtime blockiert Capability-Verletzungen vor der Ausführung.

| Domain | Maximal erlaubte Capabilities |
|---|---|
| `snake_tui`, `snake_eclipse` | `read_local_context`, `read_artifact_refs`, `read_active_task` |
| `chat_codecompass` | `read_local_context`, `read_artifact_refs`, `read_active_task`, `send_to_chat` |

`file_write`, `network_access` und `secret_access` sind für alle Domains verboten ohne explizite `elevated`-Freigabe.

---

## 6. TTL-Lease-Defaults

| Domain | Default TTL | Min | Max |
|---|---|---|---|
| `snake_tui` | 7 s | 1 s | 60 s |
| `snake_eclipse` | 7 s | 1 s | 60 s |
| `chat_codecompass` | 15 s | 1 s | 60 s |

---

## 7. Geltungsbereich

Diese Policy gilt für:
- `client_surfaces/operator_tui/` (TUI Snake + Chat)
- `client_surfaces/eclipse_runtime/` (Eclipse Snake)
- `agent/services/heuristic_runtime/` (Backend-Runtime-Services)

Referenz-Konfiguration: `WorkerRoleConfig-v1` (siehe `schemas/worker/worker_role_config.v1.json`).
