# CodeCompass Runtime Domain Scope (CCRDS)

Status: implementiert (Retrieval-Scope aktiv nutzbar, Write-Scope als
zentraler Validator-Hook vorbereitet — siehe „Grenzen“ unten).

## Domain Discovery vs. Runtime Scope

[Domain Discovery](codecompass-domain-discovery.md) ist eine
deterministische **Analyse**-Stufe: sie erkennt Domain-Kandidaten und
schreibt `domains.detected.json`. Kandidaten sind Vorschläge mit
Begründung — **keine automatische Runtime-Freigabe**.

Der **Runtime Domain Scope** ist eine separate, Hub-owned Policy-Schicht:
Erst wenn ein User oder der Hub eine Domain **explizit auswählt**, werden
deren Pfade zur harten Grenze für Retrieval (und perspektivisch
Schreiboperationen). Das LLM bestimmt den Projektbereich nie selbst
(CCRDS-DD-002): ein Prompt wie „mach Rechnungserzeugung“ expandiert bei
gesetztem Scope `Bestellmodul` nicht in Artikelkatalog oder fremde
Integrationen.

## Drei Domain-Namespaces

| Namespace | Beispiel | Bedeutung |
|---|---|---|
| Interne Retrieval-Domains | `worker`, `codecompass` | weicher RetrievalProfile-Hint (unverändert) |
| Capability-Domains (`domains/<id>/`) | `blender`, `kicad` | Tool-/Capability-Packs, kein Pfad-Scope |
| Fachliche Code-Domains | `domain:bestellmodul` | Runtime-Scope aus Discovery/Descriptor |

Fachliche Domains werden **nur mit Prefix** `domain:<id>` im bestehenden
ai-snake-Key `chat_retrieval_domain_hint` referenziert (CCRDS-DD-006).
Unprefixte unbekannte Werte bleiben wie bisher ignorierte Profil-Hints;
Kollisionen sind damit per Konstruktion ausgeschlossen.

## Config-Keys

```bash
CODECOMPASS_DOMAIN_SCOPE_ENABLED=0        # Feature-Flag (Default: aus)
CODECOMPASS_DOMAIN_ARTIFACT_PATH=artifacts/codecompass/domains.detected.json
CODECOMPASS_DOMAIN_DESCRIPTOR_ROOT=domains
CODECOMPASS_SCOPE_STRICT_MODE=1           # fail-closed (Default: an)
CODECOMPASS_SCOPE_ALLOW_RELATION_EXPANSION=0
CODECOMPASS_SCOPE_MAX_EXTERNAL_REFERENCE_CHUNKS=2
```

Bei `CODECOMPASS_DOMAIN_SCOPE_ENABLED=0` bleibt ein `domain:`-Hint ein
weicher Hinweis (Trace-Reason `domain_hint_runtime_scope:<id>`), es wird
nichts gefiltert.

## Minimalablauf

1. User wählt im ai-snake-Config-Panel (oder per PATCH `/ai-snake/config`)
   `chat_retrieval_domain_hint = "domain:bestellmodul"`.
2. `scope_from_domain_hint()` erzeugt einen `DomainScope`
   (`selected_domain_ids=["bestellmodul"]`).
3. Der `DomainScopeResolver` lädt `domains.detected.json`
   (`codecompass_domain_analysis.v1`) und — falls vorhanden — den
   Descriptor `domains/bestellmodul/domain.json`. Descriptor-`code_paths`
   gewinnen gegen detected `root_paths`, der Konflikt wird als Warning
   protokolliert (CCRDS-005). Capability-Descriptoren ohne `code_paths`
   werden ignoriert.
4. `HybridOrchestrator.get_relevant_context(query, domain_scope=...)`
   begrenzt RepositoryMap- und AgenticSearch schon im Suchraum
   (`allowed_paths`), filtert alle Chunks hart über den
   `DomainScopeFilter` und stellt dem Prompt einen Scope-Banner voran.
5. Das Ergebnis enthält `domain_scope` mit `active_domain_ids`,
   `allowed_read_paths`, `filter_stats` (kept/dropped/dropped_reasons)
   und Provenance — UI/`/rag why` können erklären, was warum fehlt.

### API-Beispiel

```bash
# Domain-Liste (stabil sortiert)
curl -s $HUB/api/codecompass/domains

# Scope-Preview: was würde diese Auswahl erlauben?
curl -s -X POST $HUB/api/codecompass/domain-scope/preview \
  -H 'Content-Type: application/json' \
  -d '{"selected_domain_ids": ["bestellmodul"], "strict": true}'
```

Eine Preview erteilt keinerlei Freigabe (`preview_only: true`).

### Python-Beispiel

```python
from agent.codecompass.domain_scope import DomainScope
from agent.codecompass.domain_scope_resolver import DomainScopeResolver

resolver = DomainScopeResolver(repo_root=repo_root)
resolved = resolver.resolve(DomainScope(selected_domain_ids=["bestellmodul"]))
result = orchestrator.get_relevant_context(query, domain_scope=resolved)
```

## Strict Mode und Fallback

- **Strict (Default):** Unbekannte Domain, leeres/defektes Artefakt oder
  null aufgelöste Pfade ⇒ `domain_scope_violation`. Es wird **kein**
  Prompt gebaut und **nicht** still auf globales Repo-RAG zurückgefallen
  (CCRDS-DD-003). `run_with_sgpt` ruft in diesem Fall kein LLM auf.
- **Non-strict:** Unbekannte Domains erzeugen Warnings und einen leeren
  bzw. neutralen Scope — niemals erfundene Pfade.
- **Kein Treffer im Scope:** Das Ergebnis sagt „keine Treffer innerhalb
  der Domain“ (leere Chunks + aktiver Scope-Block); es wird nicht
  automatisch global gesucht. UI kann Domain-Wechsel/-Erweiterung oder
  Scope-Deaktivierung vorschlagen.

## Read/Write-Split und Relation Expansion

- `allowed_read_paths` und `allowed_write_paths` sind getrennt modelliert
  (CCRDS-DD-004). Ohne expliziten Write-Vertrag default der Write-Scope
  auf die Domain-Pfade selbst.
- Relation Expansion (`allow_external_references` +
  `max_external_reference_chunks`) darf kontrolliert wenige Chunks aus
  Nachbardomains behalten; diese sind als
  `domain_scope_external_reference=true` markiert.

## Write-Enforcement-Punkte (CCRDS-012)

| Codepfad | Einstufung |
|---|---|
| `agent/services/ananta_workspace_mutation_policy.py` → `evaluate_changed_files(domain_allowed_write_paths=...)` | direkt blockierbar (Hook implementiert, Reason `outside_domain_write_scope`) |
| `agent/common/sgpt_workspace_mutation.py:278` (ruft `evaluate_changed_files`) | braucht Adapter: `domain_allowed_write_paths` aus aktivem Scope durchreichen |
| `agent/services/tools/workspace_mutation_tools.py:401` (ruft `evaluate_changed_files`) | braucht Adapter: dito |
| `worker/core/external_adapters.py`, `worker/adapters/coding_tool_base.py` (externe Agenten: OpenCode/Codex/Aider) | braucht Approval: Änderungen laufen über Workspace-Diff → Mutation-Policy greift nachgelagert |
| `agent/services/generated_source_line_policy_service.py` | nicht relevant (Zeilen-Policy, keine Pfad-Grenze) |

Cross-domain Writes werden über `decide_cross_domain_write()` entweder
blockiert (strict) oder als `approval_required` gemappt;
`build_approval_requirement()` bindet den Grant an
`requested_path` + `arguments_digest` — nie pauschal an ein Tool
(CCRDS-013).

## Grenzen (Stand 2026-06-12)

- **Write-Scope-Enforcement ist vorbereitet, nicht flächig aktiv:** Der
  zentrale Validator-Hook in der Workspace-Mutation-Policy existiert und
  ist getestet; die Aufrufer reichen `domain_allowed_write_paths` noch
  nicht automatisch aus einem aktiven Scope durch.
- Die SemanticSearchEngine wird nachgelagert gefiltert (nicht im
  Suchraum begrenzt) — für die Korrektheit der Grenze ausreichend, für
  Performance bei sehr großen Doku-Beständen optimierbar.
- Der Approval-Flow ist auf Vertrags-/Testebene angebunden
  (Decision + digest-gebundene Requirement-Payload); die Verdrahtung in
  den `ApprovalRequestService`-Lifecycle ist Folgearbeit.
