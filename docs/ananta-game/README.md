# Ananta Strategy Game

Das Ananta Strategy Game macht die reale Hub-Worker-Architektur als spielbare Modellschicht sichtbar: Code-Territorien, Agentenrollen, Kontextgrenzen, Policies und Artefakt-verifizierter Fortschritt.

## Dokumentstruktur

- `rules.md`: Spielregeln, Mechaniken und Rollenbegriffe.
- `architecture.md`: technische Zuordnung zu Hub, Worker, CodeCompass, ContextAegis, ArtifactGuard und TrustWeave.
- `domain-model.md`: Datenmodell fuer map-/agent-/policy-/artifact-basierte Simulation.
- `codecompass-adapter.md`: Mapping-Regeln von Repository/CodeCompass zu GameMap.
- `web-ui.md`: minimaler UI-Vertrag und Demo-Rendering fuer Angular/Web.
- `tui.md`: terminaltaugliches Darstellungskonzept inklusive Fallbacks.
- `testing.md`: Golden-/Integrations-Testplan fuer die Kernmodule.

## Kernbegriffe

Alle Kernbegriffe bleiben deckungsgleich mit der Produktarchitektur:

- CodeAegis
- AegisFlow
- AegisHub
- AgentAegis
- DevAegis
- ContextAegis
- ArtifactGuard
- TrustWeave
- CodeCompass
- NagaCore
