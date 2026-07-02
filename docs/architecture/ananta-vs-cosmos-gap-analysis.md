# Ananta vs. Augment Cosmos — Strategische Gap-Analyse

Erstellt: 2026-07-02
Internes Strategiedokument — nicht für externe Veröffentlichung.
Bezug: COSMOS-000

---

## 1. Cosmos als Referenzsystem

Augment Cosmos ist kein reines Retrieval- oder IDE-Plugin, sondern eine kommerzielle
Agentenplattform für Softwareteams. Die Architektur umfasst:

- **Agent Runtime** — verwaltete Agentenläufe mit State, Artefakten, Fehlern, Resume und Audit
- **Context Engine** — Live Knowledge Graph über Repos, IDEs, History und Integrationen
- **Expert Registry** — vorgefertigte Agentenrollen: Triage, PR Author, Reviewer, Risk Analyst, Tester
- **Trigger/Automation** — GitHub Events, Issues, CI-Status, Zeitpläne starten Workflows
- **Shared File System** — task-gebundene Arbeitsbereiche für Diffs, Logs, Reports
- **Sandboxes** — isolierte Ausführung von Builds, Tests und schreibenden Agentenoperationen
- **Governance** — RBAC, Audit, Policy, SIEM-Export, Organisationsgrenzen

Cosmos ist damit kein CodeCompass-Konkurrent, sondern ein **Ananta-ähnliches kommerzielles
Agentensystem**. CodeCompass entspricht nur der Context Engine — einer Teilkomponente.
Die Gap-Analyse muss deshalb auf beiden Ebenen geführt werden: Gesamtplattform und Kontext-Layer.

---

## 2. Wahrscheinliche externe Stärken (ehrlich)

Einschätzung auf Basis öffentlicher Dokumentation. Keine inneren Metriken verfügbar.

| Bereich | Einschätzung |
|---|---|
| Produktreife UX | Produktionsreif für mittlere bis große Teams, UX-Investition sichtbar |
| Expert-Integrationen | Fertige Rollen für GitHub/Jira/Slack/CI, sofort nutzbar |
| Context Engine Tiefe | Live Knowledge Graph, kontinuierliche Indexierung, IDE-Kontext |
| Replayability | Runs inspizierbar, Replay-Infra vorhanden laut Dokumentation |
| Enterprise Governance | RBAC, Audit, SSO, vermutlich SOC2-Konformität im Fokus |
| History Context | Git-History, PR-Kontext, Team-Aktivitätssignal integriert |

---

## 3. Anantas Differenzierung (technisch)

### Self-host-first + vollständig lokal nutzbar

Ananta läuft ohne externe SaaS-Abhängigkeit. Hub, Worker, CodeCompass und Datenbank laufen
lokal (Docker Compose). Modell-Provider sind austauschbar: Ollama, LM Studio, OpenAI-kompatibler
Endpunkt oder cloud-basierte APIs — per Config, kein Hardcoding.

### Default-Deny als Grundprinzip

Worker bekommen minimale Rechte. Jede schreibende Aktion (Diff anwenden, Git-Op, PR erstellen,
externes Tool aufrufen) erfordert ein explizites Policy Gate. Default-Deny ist kein
Enterprise-Feature, sondern Kern-Invariante. Siehe: `docs/architecture/workflow-security.md`.

### Hub als einzige Orchestrierungsebene

Kein Worker kommuniziert direkt mit einem anderen Worker. Hub kontrolliert Planung,
Delegation, Context-Freigabe und Artefaktfluss. Keine unkontrollierte Agenten-zu-Agenten-
Kommunikation. Dieses Prinzip ist im Systemmodell verankert, nicht optional.

### Policy- und Rechtegraph explizit im Systemmodell

Erlaubte Pfade, Tools und Operationen sind pro Worker/Expert im Policy-Scope modelliert.
PolicySnapshot wird pro Run persistiert — nachvollziehbar, nicht nur zur Laufzeit geprüft.

### Erklärbarer CodeCompass-Graph statt Blackbox-Retrieval

CodeCompass liefert Kontexttreffer mit Quelle, Datei, Symbol und Abrundungsgrund.
Domain Map, Funktionsgraph und RAG-Treffer sind einsehbar. Kein opakes Embedding-Retrieval
ohne Rückverfolgbarkeit. Siehe: `docs/architecture/codecompass-vs-context-engine.md`.

### Transparente Laufzeit

PolicySnapshot, ContextTrace und ToolCallLog sind vorgesehene Artefakte pro Run.
Nachvollziehbarkeit ist Designziel, nicht nachträgliche Auditfunktion.

### Austauschbare Modell-Provider

Provider-Schicht trennt LLM-Calls von Geschäftslogik. Lokale Modelle (Ollama/LM Studio)
und Cloud-Anbieter wechseln per Config ohne Code-Änderung.

---

## 4. Gap-Tabelle

| Cosmos-Fähigkeit | Ananta-Status |
|---|---|
| Agent Runtime (robuste State Machine) | Partiell — Hub/Worker vorhanden, kein vollständiges Zustandsmodell |
| Expert Registry | Fehlt — kein versioniertes, policy-fähiges Expert-Template-System |
| Trigger / Automation | Fehlt — kein Event-basierter Workflow-Start |
| Shared File System (typisierte Artefakte) | Partiell — Artefakte existieren, kein vollständiges Typ/Run-Modell |
| Sandboxes | Fehlt — keine Sandbox-Abstraktion, nur lokale Prozesse |
| Human-in-the-loop Gates | Partiell — Konzept vorhanden, kein einheitliches Gate-System |
| Replayable Runs | Fehlt — kein ReplayRecord-Schema, kein Dry-Run-Modus |
| Live Knowledge Graph | Fehlt — CodeCompass hat Domain Map und Symbol-Graph, kein Live-Update |
| Cross-Repo-Analyse | Fehlt |
| History Context (Git/PR/Issues) | Fehlt |
| Context Curation Pipeline | Fehlt — kein explizites Retrieve→Rank→Compress→Trace-System |
| Governance / RBAC | Partiell — Least-Privilege-Konzept, kein vollständiges Rollenmodell |

---

## 5. Priorisierung der Lücken

### Kurzfristig (Fundament schließen)

- **Expert Registry** — ohne strukturierte Expert-Definitionen bleibt Planung ad-hoc
- **Task Artifact Model** — typisierte, run_id-gebundene Artefakte ermöglichen alle anderen Features
- **Human-in-the-loop Policy Gates** — einheitliche Gate-Infrastruktur statt Einzellösungen
- **Context Curation Pipeline** — Retrieval-Qualität vor Knowledge-Graph-Ausbau verbessern

### Mittelfristig (nächste Ausbaustufe)

- **Agent Runtime State Machine** — robuste Zustandsmodellierung für Lauffehler, Retry, Cancel
- **Sandbox-Abstraktion** — Port + FakeSandbox, Docker optional
- **Knowledge Graph Schema** — versioniertes Schema vor Live-Update-Infrastruktur
- **Replayable Runs** — ReplayRecord + Dry-Run auf Basis stabiler Artefakte

### Längerfristig (wenn Fundament stabil)

- **Cross-Repo-Graph** — aufbauend auf Knowledge Graph Schema
- **GitHub Trigger + PR Draft Expert** — aufbauend auf Expert Registry + HITL Gates
- **History Context** — aufbauend auf Knowledge Graph
- **Enterprise Governance / RBAC** — aufbauend auf PolicySnapshot + Audit-Events

---

## 6. Wo Augment wahrscheinlich stärker bleibt

Ohne gegenteilige Ressourcen bleibt Augment vermutlich besser in:

- **Produktpolitur für große Teams** — UX-Investition, Onboarding, Support-Infrastruktur
- **Fertigen CI/GitHub-Integrationen** — sofort einsatzbereit ohne Eigenimplementierung
- **Knowledge Graph Reife** — kontinuierliche Indexierung über viele Repos, IDE-Aktivitätskontext
- **Enterprise Compliance** — SSO, SOC2, SIEM-Export als Produktfeature

Ananta ist hier bewusst anders positioniert: kontrollierbar, offen, lokal, erklärbar —
nicht feature-kompetitiv mit einem vollfinanzierten SaaS-Produkt.

---

*Verwandte Dokumente:*
- `docs/architecture/codecompass-vs-context-engine.md`
- `docs/architecture/ananta-product-positioning.md`
- `docs/architecture/ananta-roadmap.md`
- `docs/architecture/workflow-security.md`
