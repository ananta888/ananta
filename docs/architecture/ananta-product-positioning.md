# Ananta — Produktpositionierung

Erstellt: 2026-07-02
Internes Strategiedokument. Basis für README-Aktualisierung und Website-Texte.
Bezug: COSMOS-022

---

## 1. Was Ananta ist

Ananta ist ein **lokales, transparentes, policy-fähiges Agenten-Betriebssystem für
Softwarearbeit**. Es verbindet Hub-Worker-Orchestrierung, deterministischen Projektkontext
durch CodeCompass und Least-Privilege-Policies so, dass KI-Agenten produktiv arbeiten
können, ohne blind Zugriff auf Code, Secrets oder Infrastruktur zu bekommen.

Das Systemmodell: `Goal → Plan → Task → Execution → Verification → Artifact`.
Der Hub kontrolliert jeden Schritt. Worker führen aus. Ergebnisse sind nachvollziehbar.

---

## 2. Was Ananta nicht ist

- Kein Blackbox-SaaS — keine erzwungene Cloud-Abhängigkeit, kein opakes Embedding-Retrieval
- Kein Agent-as-a-Service — keine vollautomatischen Agenten ohne Policy-Gates
- Kein Cosmos-Klon — kein Versuch, kommerzielle Enterprise-Features eins zu eins nachzubauen
- Kein "Chatbot mit Tools" — kein REPL-Modus ohne Planung, keine unkontrollierte Tool-Ausführung
- Keine Scheinsicherheit — Ananta garantiert keine vollständige Absichtserkennung über
  beliebig zerlegte Aufgaben; menschliche Verantwortung für Ziel und Zusammenbau bleibt

---

## 3. Kernthesen mit technischen Belegen

### "Open/self-host-first"

Ananta läuft vollständig lokal via Docker Compose. Keine externen Accounts erforderlich.
Hub, Worker, CodeCompass-Indexierung, Datenbank und Artefaktspeicherung laufen im
eigenen Netz. Cloud-Modell-Provider sind optional und per Config wechselbar.

Beleg: `docker-compose.yml`, Bootstrap-Install-Doku, kein Hardcoding auf externe Endpunkte.

### "Policy-first and default-deny"

Hub entscheidet über jede schreibende Aktion. Worker bekommen minimale Rechte (`allowed_paths`,
`allowed_tools`) per Policy-Scope. Schreiboperationen (Diff anwenden, Git-Ops, PR erstellen,
Netzwerkzugriff, Secrets) erfordern explizite Gates. Default ist Ablehnung, nicht Erlaubnis.

Beleg: `docs/architecture/workflow-security.md`, `docs/architecture/workflow-gates.md`,
`docs/architecture/worker-governed-executor-adr.md`.

### "Explainable context"

CodeCompass liefert Kontexttreffer mit konkretem Ursprung: Datei, Zeile, Symbol, Domäne.
Domain Map und Funktionsgraph sind einsehbar. Retrieval-Entscheidungen sind nicht opak.

Beleg: `docs/architecture/codecompass-worker-boundaries.md`,
`docs/architecture/source-pack-codecompass-architecture.md`.

### "Human-approved impact"

Dateiänderungen, Git-Operationen und PR-Erstellungen durchlaufen explizite Approval-Gates.
DiffProposal ist die einzige Vorstufe für Schreiboperationen — kein direktes Schreiben
ohne Prüfung. Freigabeentscheidungen werden unveränderlich auditiert.

Beleg: `docs/architecture/workflow-gates.md`, ADR Worker Governed Executor.

### "Local models + external providers both supported"

Provider-Schicht ist ein austauschbarer Port. Unterstützte Backends: Ollama (lokal),
LM Studio (lokal), OpenAI-kompatibler Endpunkt, konfigurierbare Cloud-APIs.
Wechsel per Config ohne Code-Änderung. Keine Modell-Abhängigkeit im Kern.

Beleg: `docs/architecture/model-routing-current-state.md`,
`docs/architecture/model-routing-profiles-policy.md`.

---

## 4. Abgrenzung zu kommerziellen Tools

| Dimension | Ananta | Kommerzielle SaaS-Systeme (z.B. Cosmos) |
|---|---|---|
| Hosting | Lokal oder selbst gehostet | Cloud-Pflicht oder Hybrid-Option |
| Modell-Provider | Frei wählbar, lokal möglich | Oft an Anbieter-Cloud gebunden |
| Kontext-Transparenz | Erklärbare Treffer mit Quelle | Teils opakes Embedding-Retrieval |
| Policy-Modell | Default-Deny, explizit im System | Enterprise-Feature, oft optional |
| Orchestrierung | Hub-kontrolliert, kein Agent-zu-Agent | Teils direkte Agenten-Kommunikation |
| Datenschutz | Code verlässt Netz nur auf Anforderung | Indexierung remote, oft unvermeidbar |
| Zielgruppe | Einzelentwickler, kleine Teams, kontrollierte Umgebungen | Mittlere bis große Teams, Enterprise |
| Reife | PoC/Beta-Qualität, aktiver Aufbau | Produktionsreif, Support-Infrastruktur |

Die Abgrenzung ist technisch und sachlich. Kommerzielle Systeme haben reale Stärken
(Produktpolitur, fertige Integrationen, Support). Ananta ist dort bewusst anders, nicht
besser in jeder Dimension.

---

## 5. Für wen Ananta sinnvoll ist

**Einzelentwickler** die KI-Agenten für eigene Projekte nutzen wollen, ohne Code an
externe Dienste zu senden. Lokale Modelle via Ollama/LM Studio reichen für viele Aufgaben.

**Kleine Teams** die nachvollziehbare Agentenarbeit mit expliziten Approval-Gates brauchen —
kein automatisches Ausführen ohne Kontrolle.

**Datenschutzkritische Projekte** bei denen Code, Kontext und Arbeitsergebnisse das eigene
Netz nicht verlassen dürfen. Self-host ist keine Option, sondern Anforderung.

**Experimentelle Workflows** bei denen die Architektur des Agentensystems selbst
Forschungsgegenstand ist. Ananta ist offen genug, um neue Expert-Typen, Provider und
Policy-Modelle zu erproben.

**Was Ananta nicht ersetzen kann**: Produktionsreife Enterprise-Governance, fertige
CI/GitHub-Integrationen ohne Eigenaufwand, UX-Politur für große Teams oder
24/7-Support-Infrastruktur. Wer das braucht, sollte kommerziellen Systemen den Vorzug geben.

---

*Verwandte Dokumente:*
- `docs/architecture/ananta-vs-cosmos-gap-analysis.md`
- `docs/architecture/ananta-roadmap.md`
- `docs/architecture/workflow-security.md`
- `README.md` — Sicherheitsgrenze und Nicht-Ziel-Abschnitt
