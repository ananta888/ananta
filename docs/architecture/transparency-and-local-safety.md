# "Nicht vertrauen. Prüfen." — Anantas Ansatz für prüfbare Agentenarbeit

> TRANS-001 | Transparenz-Manifest | Status: open

## Kernaussage

Ananta fordert kein Vertrauen in Modell, Provider oder Worker. Jede relevante
Entscheidung erzeugt nachvollziehbare Artefakte, die der Nutzer selbst prüfen kann.

Agentensysteme, die Vertrauen einfordern, sind nicht sicherer als Systeme ohne Audit.
Ananta wählt den anderen Weg: sichtbare Grenzen, explizite Delegation, lokale
Kontrolle, prüfbare Spuren.

---

## 6 Core-Prinzipien

### P1 — Local-first

Normale Nutzung funktioniert ohne externe Provider. Ollama und LM Studio sind
primäre Modell-Backends. Externe Provider wie OpenAI oder OpenRouter sind Adapter,
keine Grundlage.

**Artefakt-Nachweis**: `PolicySnapshot.model_policy` enthält den genutzten Provider
und belegt, ob ein lokales Modell verwendet wurde.

---

### P2 — Verifiable by default

Jeder Run erzeugt ein Set prüfbarer Trace-Artefakte:

- `PolicySnapshot` — welche Rechte galten
- `ContextTrace` — welcher Kontext wurde warum gewählt oder verworfen
- `ToolCallLog` — welche Tools wurden mit welchem Ergebnis aufgerufen
- `DelegationTrace` — warum wurde welcher Worker gewählt

Diese Artefakte werden nicht nachträglich erzeugt, sondern sind integraler Teil
jedes Runs.

**Artefakt-Nachweis**: Jeder Run hat eine `run_id`, auf die alle vier Trace-Typen
verweisen. Fehlende Traces blockieren Worker-Ausführung.

---

### P3 — Default-Deny

Fehlende Rechte führen zur Blockade. Nicht zur impliziten Erlaubnis, nicht zum
stillen Fallback, nicht zur degradierten Ausführung ohne Hinweis.

**Artefakt-Nachweis**: `ToolCallLog.policy_decision` enthält `blocked` mit
`reason_code` wenn ein Call blockiert wurde. Blockierte Aktionen erscheinen im
Run Report unter `blocked_actions`.

---

### P4 — Explicit Delegation

Der Hub delegiert bewusst. Ein Worker bekommt genau: Aufgabe, Kontext-Paket,
und Rechte für diesen Schritt. Nichts darüber hinaus.

**Artefakt-Nachweis**: `DelegationTrace` dokumentiert Ziel, gewählten Worker,
übergebenen PolicyScope und verworfene Alternativen mit Ablehnungsgrund.

---

### P5 — No hidden authority

Kein Modell, kein Provider, kein Worker ist autoritative Quelle für
Codeänderungen, Policy-Entscheidungen oder Freigaben. Diese Autorität liegt
ausschließlich beim Hub, gestützt durch PolicyEngine und explizite Nutzerfreigaben.

**Artefakt-Nachweis**: Alle Entscheidungen verweisen auf `policy_scope_id`,
nicht auf Modell-Output. Modell-Aussagen und belegte Evidence werden im
Run Report getrennt ausgewiesen.

---

### P6 — Human-approved impact

Dateiänderungen, Git-Operationen, PRs, Deployments und externe Calls erfordern
explizite Approval-Gates. Kein Agent wendet Änderungen direkt auf echte Repos an.

**Artefakt-Nachweis**: `ApprovalRecord` enthält Zeitstempel, approving_entity,
scope und den Verweis auf das freigegebene `DiffProposal`. Kein Apply ohne Record.

---

## Prüfbare Artefakte

| Artefakt | Inhalt | Zweck |
|---|---|---|
| `PolicySnapshot` | Rechte, Pfade, Tools, Provider, Modelle, Gates zum Run-Startzeitpunkt | Belegt, welche Regeln galten |
| `ContextTrace` | Query, Provider, Treffer, Auswahlgrund, verworfene Kandidaten mit Grund | Erklärt, warum welcher Kontext verwendet wurde |
| `ToolCallLog` | tool_name, input_hash, output_hash, policy_decision, Dauer, Status | Nachweis über jeden Tool-Aufruf |
| `DelegationTrace` | Ziel, gewählter Worker, Alternativen, Auswahlgrund, PolicyScope | Zeigt warum welcher Worker gewählt wurde |
| `DiffProposal` | Dateiliste, Hunks, Risikoscore, Ursprung | Alle Änderungen als prüfbarer Vorschlag |
| `ApprovalRecord` | Wer, wann, was freigegeben, Verweis auf DiffProposal | Nachweis jeder Freigabe |

Alle Artefakte können als JSON exportiert und ohne Modell-Vertrauen gelesen werden.

---

## Anti-Claims

Was Ananta nicht ist:

- **Nicht Blackbox Coding SaaS**: Jede Entscheidung ist durch Artefakte belegbar,
  nicht nur durch Modell-Aussagen.
- **Nicht pauschal-autorisierter Agent**: Worker bekommen keinen generellen
  Repo- oder Tool-Zugriff. Scope gilt pro Schritt.
- **Nicht Sicherheit nur als Enterprise-Feature**: Default-Deny, Traces und
  Approval-Gates gelten für alle Nutzungsmodi, nicht erst ab einem Tarif.
- **Nicht Cloud-Kontext als unsichtbare Wahrheit**: Externe Provider sind markiert,
  ihr Beitrag ist im ContextTrace sichtbar und separat ausgewiesen.
- **Nicht selbst-autorisierende Worker**: Kein Worker darf Rechte oder Kontext
  selbst erfinden oder erweitern.

---

## Abgrenzung zu proprietären Systemen

Proprietäre Agentenplattformen bieten oft höhere Produktreife, tiefere IDE-Integration
und breitere Modellauswahl. Diese Stärken sind real.

Ananta wählt eine andere Gewichtung: Kontrollierbarkeit vor Komfort,
Transparenz vor Geschwindigkeit, lokale Prüfbarkeit vor Cloud-Convenience.

Der Unterschied ist technisch messbar:

| Eigenschaft | Proprietäre Plattform | Ananta |
|---|---|---|
| Kontext-Herkunft sichtbar | meistens nein | ja, per ContextTrace |
| Worker-Auswahl nachvollziehbar | nein | ja, per DelegationTrace |
| Lokaler Betrieb ohne Cloud | selten | ja, per local_only-Modus |
| Policy-Snapshot pro Run | nein | ja, unveränderlich |
| Approval-Gate für jede Änderung | optional/Enterprise | Standard |

Ananta ist kein vollständiger Ersatz für produktreife Coding-Assistenten.
Es ist ein transparentes Agenten-Betriebssystem für Nutzer, die Kontrolle
über Vertrauen stellen.

---

## Weiterführend

- `docs/architecture/local-only-mode.md` — Local-only als Produktfeature
- `docs/architecture/verification-bundle.md` — Public Verification Bundle
- `docs/architecture/augment-auggie-integration.md` — Optionale externe Provider
- `docs/security/augment-threat-model.md` — Threat Model für externe Integrationen
