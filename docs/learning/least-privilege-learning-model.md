# Least Privilege Learning Model

## Grundprinzip

Lernfortschritt und Rechtefreigabe sind gekoppelt: mehr Rechte gibt es nur nach nachweislich bestandenen Schritten.

## Startzustand

- Neue Lernpfade beginnen mit minimalen Rechten (`view`).
- Keine implizite Freigabe fuer Worker-Nutzung, Decrypt oder Remote-LLM.

## Progressive Freischaltung

- Freischaltungen entstehen durch:
  - bestandene deterministische Checks
  - explizites Human Approval bei sensitiven Szenarien
- Jede Freischaltung ist zeitlich/sachlich begrenzt.

## Rechteklassen

- **User-Rechte** (lesen, Uebungen ausfuehren)
- **Team-/Mentor-Rechte** (reviewen, overrides mit Audit)
- **Worker-Rechte** (kontextspezifische Ausfuehrung)
- **Artifact-Rechte** (`download_encrypted`, `decrypt`, `share`)
- **Remote-LLM-Rechte** (immer separat, nie implizit)

## Sicherheitsregel

Default-Deny und explizite Grants bleiben der Standard fuer jeden Kurs- und Uebungsuebergang.
