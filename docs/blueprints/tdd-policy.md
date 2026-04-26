# TDD Policy and Approval Mapping

Dieses Dokument beschreibt, wie der `TDD`-Blueprint bestehende Hub-Policies nutzt.

## Grundsatz

Der TDD-Blueprint lockert keine Sicherheits- oder Governance-Regeln. Er nutzt dieselben Policy- und Approval-Pfade wie andere coding-orientierte Blueprints.

## Mapping

1. **Test-Aenderungen sind Schreibvorgaenge**
   - Neue/angepasste Tests gelten als write activity und folgen normalen Patch-Regeln.
2. **Patch Apply bleibt approval-gated**
   - Wenn Policy eine Freigabe verlangt, darf kein direkter Apply ohne Approval erfolgen.
3. **Testausfuehrung ueber Worker-Capability**
   - Testlauf erfolgt ueber `worker.test.run` innerhalb der erlaubten Command-Policy.
4. **Verifikation explizit**
   - Abschluss nutzt `worker.verify.result` mit nachvollziehbaren Evidence-Refs.

## Red/Green Semantik

- **Red**: erwartete Fehlerevidenz in der Red-Phase.
- **Green**: Pass-Evidenz nach minimalem Patch.
- Kein Schritt darf Red-Evidenz als Green-Erfolg maskieren.
