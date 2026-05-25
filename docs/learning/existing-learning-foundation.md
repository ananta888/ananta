# Existing Learning and Onboarding Foundation

## Bestand im Repository

Das Repo hat bereits eine brauchbare Basis fuer ein Lernsystem, auch wenn noch kein vollstaendiges Course/Lesson/Exercise/Assessment-System existiert.

## Vorhandene Bausteine

1. `frontend-angular/src/app/components/onboarding-checklist.component.ts`  
   Basis fuer First-Run, Checklisten und Blueprint-Empfehlungen.
2. `agent/services/demo_mode_service.py`  
   Read-only Demo-Beispiele als CoursePreview-nahe Grundlage.
3. `frontend-angular/src/app/components/instruction-layers-workbench.component.ts`  
   Instruction Profiles und Overlays fuer kontextbezogene Guidance.
4. `agent/db_models.py`  
   Vorhandene Modelle fuer Artefakte, Kontext-Bundles, Policies, Teams/Rollen/Templates und Terminal-Audit.

## Sicherheits- und Auditbasis

- `ArtifactDB` / `ArtifactVersionDB`
- `ContextBundleDB` / `ContextAccessPolicyDB`
- `TerminalSessionDB` / `TerminalEventDB`

Diese Bausteine bilden eine tragfaehige Sicherheits- und Nachvollziehbarkeitsbasis fuer Lernpfade mit Least-Privilege.

## Klare Abgrenzung

- **Vorhanden:** Onboarding, Demo-Flows, Instruction-Layers, Security- und Auditbausteine.
- **Noch nicht als eigenes System vorhanden:** echtes Course/Lesson/Exercise/Assessment/Progress-Modell mit Freischaltungen.
