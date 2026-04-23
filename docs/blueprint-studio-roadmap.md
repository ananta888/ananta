# Advanced Studio Roadmap (separat vom Standard-UX)

Dieses Dokument sammelt weiterfuehrende Studio-/Admin-Ideen getrennt vom kompakten Produktweg.

Ziel: Die Hauptnutzung bleibt bewusst einfach (Blueprint waehlen, Team starten, arbeiten), waehrend tiefe Bearbeitungsfunktionen separat planbar bleiben.

## Nicht-Ziel fuer den Standardfluss

- Kein Zwang fuer normale Nutzer, Studio-Funktionen verstehen zu muessen.
- Keine Vermischung von Rollout-/Migrationsdetails mit Erstnutzer-Dokumentation.
- Kein Blocker fuer die laufende UX-Vereinfachung.

## Geplante Advanced-Themen

1. Visueller Blueprint-Diff mit Rollup auf Rollen-, Artefakt- und Policy-Ebene.
2. Multi-Blueprint-Refactoring mit Vorschau fuer Massenanpassungen.
3. Reconcile-Workbench mit expliziter Genehmigungsstrecke.
4. Erweiterte Governance-Simulation (safe/balanced/strict) vor Instanziierung.
5. Studio-Pipelines fuer Bundle-Import/Export inkl. Dry-Run-Checks.

## Trennlinie Standard vs. Studio

- **Standard-UX** bleibt bei produktnahen Begriffen (Blueprint, Team, erwartete Outputs).
- **Studio-UX** darf interne Konzepte explizit machen (snapshot, drift, reconcile, migration constraints).
- Dokumentation bleibt entsprechend getrennt verlinkt.

## Verweise

- Standardnutzer: `docs/blueprint-product-model.md`, `docs/standard-blueprints.md`
- Admin-Betrieb: `docs/blueprint-admin.md`, `docs/blueprint-migration-rollout.md`
