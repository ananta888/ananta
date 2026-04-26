# TDD Task Decomposition (Red -> Green -> Refactor -> Verify)

Dieses Dokument definiert die standardisierte TDD-Zerlegung fuer den Blueprint `TDD`.

## Ziel

TDD wird als normaler Hub-Taskfluss modelliert: keine Sonderorchestrierung, keine direkten Worker-Bypasse.

## Sequenz

1. **Behavior klaeren**
   - Erwartetes Verhalten und Grenzen als TestPlanArtifact erfassen.
2. **Test zuerst**
   - Test neu anlegen oder anpassen, bevor Implementierungslogik geaendert wird.
3. **Red-Phase**
   - Test ausfuehren und erwartete Fehlersignale als RedTestResultArtifact erfassen.
   - Red ist in dieser Phase erwartete Evidenz, kein Abschlussfehler.
4. **Minimaler Patch**
   - Kleinste notwendige Aenderung umsetzen und als PatchPlanArtifact referenzieren.
5. **Green-Phase**
   - Test erneut ausfuehren; Ergebnis als GreenTestResultArtifact erfassen.
6. **Optionaler Refactor**
   - Nur mit erhaltenem Green-Status; RefactorChecklist dokumentieren.
7. **Finale Verifikation**
   - Abschlusspruefungen und VerificationArtifact-Referenzen dokumentieren.

## Degraded-Verhalten

Wenn ein Projekt-Setup keine testbare Ausfuehrung erlaubt:

- TDD-Zyklus bleibt transparent als `degraded`.
- Mindestens TestPlanArtifact muss erzeugt werden.
- Implementierungs-/Apply-Schritte bleiben blockiert oder approval-gated.

## Determinismus fuer Planner-Tests

Template-basierte Planner-Tests verwenden deterministische, fixture-basierte Sequenzen (kein Live-LLM erforderlich).
