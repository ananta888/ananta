# TDD Blueprint Guide

Dieses Dokument erklaert, wie der `TDD`-Blueprint in Ananta verwendet wird.

## TDD Goal starten

1. Team mit Blueprint `TDD` erstellen oder aus dem Blueprint-Katalog instanziieren.
2. Ziel mit klarer Verhaltensbeschreibung formulieren (Was soll sich fuer Nutzer sichtbar aendern?).
3. Hub zerlegt danach in den erwarteten Ablauf: TestPlan -> Red -> Patch -> Green -> Verify.

## Red / Green / Refactor Evidenz

- **Red**: Ein fehlschlagender Test ist in der Red-Phase erwartete Evidenz (`red_expected`).
- **Patch**: Implementierungsdiff wird als separates Artefakt abgelegt und bleibt approval-gated.
- **Green**: Nach Apply muss derselbe Testlauf als `green_passed` nachweisbar sein.
- **Refactor**: Optional; nur nach Green. Falls ausgelassen, wird das explizit dokumentiert (`refactor_skipped`).
- **Verify**: Abschluss artefaktiert Test-/Patch-Evidence und finale Verifikation.

## Wenn Tests nicht laufen koennen

Wenn ein Projekt aktuell nicht testbar ist (z. B. fehlende Runtime, defekte Abhaengigkeiten):

- Der Ablauf wird transparent als **degraded** markiert.
- Red darf nicht stillschweigend uebersprungen werden; es braucht eine degradierte Begruendung.
- Patch-Apply bleibt weiterhin policy-/approval-gated.
- Ziel kann als blockiert oder degraded abgeschlossen werden, aber nicht als Green-Erfolg.

## Sichtbarkeit in CLI / TUI / Web

- **CLI** zeigt Task- und Artefaktpfade inkl. Red/Green-Status und Verifikationsreferenzen.
- **TUI** zeigt denselben Hub-Taskzustand inkl. Evidence-Refs, ohne eigene Orchestrierungslogik.
- **Web/API** liefert dieselben Blueprint-/Task-/Artefaktdaten ueber die bestehenden Endpunkte.

Alle Surfaces lesen den gleichen Hub-Status; keine Client-Surface orchestriert den TDD-Flow selbst.

## Limitierungen

- Der Blueprint erzwingt keine Magie: ohne valide Tests gibt es keinen echten Green-Nachweis.
- Mocked/fixture-basierte Smoke-Laeufe sind nur fuer deterministische Runtime-Checks gedacht.
- TDD lockert niemals Hub-Policy, Command-Policy oder Approval-Regeln.
