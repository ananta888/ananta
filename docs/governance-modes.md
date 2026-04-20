# Governance-Modi (safe / balanced / strict)

Diese Seite definiert benannte Governance-Modi als Produktfeature. Sie machen das Kontrollniveau explizit und diskutierbar, statt Governance nur als implizite Konfiguration zu behandeln.

Hinweis: Der Modus ist eine **Policy-/Profilentscheidung**. Er aendert nicht die Hub-Worker-Architektur, sondern steuert Defaults fuer:

- Tool-Grenzen / Action-Packs
- Review-Pflichten / Freigabeanforderungen
- Exposure- und Zugriffspolitik (wo relevant)
- Auditierbarkeit und Erklaerbarkeit (Explainability)

## safe

**Intent:** konservative Defaults, wenig Risiko.

- Tool-Nutzung eher eingeschraenkt.
- Review/Verification eher frueh sichtbar.
- UI erklaert Blockierungen mit naechster Aktion.

## balanced

**Intent:** sinnvoller Standard fuer Teams.

- Explizite Policies, aber nicht maximal restriktiv.
- Gute Explainability, klare Golden-Paths.

## strict

**Intent:** maximale Kontrolle, minimale Flaeche.

- Strikte Tool-Grenzen, klare Freigabe- und Review-Pflichten.
- Audit und Policy-Entscheidungen stehen im Vordergrund.

## Implementierungs-Hinweis

Im Code wird `governance_mode` als additive Config-Eigenschaft gefuehrt und im Read-Model sichtbar gemacht. Die harte Policy-Durchsetzung bleibt weiterhin in expliziten Policy-/Config-Bloecken (z.B. `exposure_policy`, `terminal_policy`, `action_packs`) verankert, damit keine versteckten Seiteneffekte entstehen.

