# Composite Risk Review

Composite Risk Review ist ein optionaler, explizit aufgerufener Audit-Layer.
Er prueft Goal-, Task-, Artefakt- und Audit-Metadaten mit kleinen,
deterministischen Regeln. Der Name ist bewusst nicht „Composite Intent
Detection“: Aus verteilten Teilschritten laesst sich keine belastbare
Gesamtabsicht garantieren.

> Composite Risk Review ist nur ein optionaler Risiko-Hinweis. Ananta kann
> keine vollstaendige Absichtserkennung ueber beliebig zerlegte Aufgaben
> garantieren. Keine Warnung bedeutet nicht, dass ein Goal, eine Task-Kette
> oder ein Artefakt sicher ist.

| Kann warnen | Kann nicht garantieren |
| --- | --- |
| Viele sicherheitsrelevante Artefakte in einer Kette | Vollstaendige Absichtserkennung |
| Kombination aus Auth, Netzwerk, Payload und Deployment | Sicherheit bei fehlendem Indikator |
| Erklaerbarer Scope-Wechsel | Korrektheit oder Harmlosigkeit eines Goals |
| Finaler Assembly-Schritt nach mehreren Teilartefakten | Automatische Allow-/Deny-Freigabe |

Der Manhattan-Projekt-Vergleich ist nur eine Metapher fuer
Kompartimentierung: Ein Beteiligter kann einen begrenzten Teilschritt sehen,
ohne das spaetere Gesamtbild zu kennen. Er ist keine technische Gleichsetzung
und keine Aussage ueber konkrete Ananta-Nutzung.

## Aktivierung und Headless-Betrieb

Standardmaessig sind `COMPOSITE_RISK_REVIEW_ENABLED=false` und
`COMPOSITE_RISK_REVIEW_EXPLICIT_ONLY=true`. Der normale Task-, Planning- und
Worker-Flow ruft den Service nicht auf und wird von ihm weder freigegeben noch
blockiert.

Nach expliziter Aktivierung nimmt der Hub
`POST /api/security/composite-risk-review` entgegen. Der JSON-Payload enthaelt
`explicit_request: true` sowie optional `goal`, `tasks`,
`artifacts_metadata` und `audit_events`. Die Antwort enthaelt immer den
Warnhinweis, `risk_level`, erklaerbare `indicators`, `explanation` und eine
`recommended_action`. Erlaubte Empfehlungen sind reine Audit-Hinweise oder
automatisierte Folgepruefungen; sie sind keine Sicherheitsfreigabe.

Der gesamte Pfad ist headless. CLI, API und Tests warten nie auf Klicks,
Eingaben oder menschliche Freigaben. Eine optionale menschliche Einsicht in
das Ergebnis bleibt moeglich, ist aber keine technische Voraussetzung. Wenn
eine externe Policy automatische Eskalation nicht erlaubt, muss der
aufrufende Workflow mit einem begrenzten maschinenlesbaren Ergebnis enden.

```bash
COMPOSITE_RISK_REVIEW_ENABLED=true \
  ananta security composite-risk-review --input review-payload.json --json
```

Das getrennte Admin-Panel `/composite-risk-review` ist nur ein optionaler
Client derselben API. Es verwendet keine Gruen-/Rot-Sicherheitsampel.

## Erklaerbare Regeln und Datenschutz

Die Regeln brauchen kein LLM und kein Netzwerk. Evidence besteht aus
bereitgestellten Task-/Artefaktreferenzen und gematchten Begriffen. Der
Audit-Eintrag speichert nur Stufe, Indicator-IDs und Mengen, nicht den rohen
Goal-, Task- oder Artefaktinhalt. Scores und Stufen sind Hinweise, keine
Allow-/Deny-Entscheidung. Die Betreiberverantwortung fuer Ziel, Kontext und
Zusammenbau bleibt bestehen.
