# RLM Recursive Context

Optional recursive analysis on top of CodeCompass hybrid retrieval and
hierarchical architecture handles. Feature flag: `codecompass_rlm_enabled`.

Simple questions stay on the normal planner. Complex questions get a
bounded plan (depth/fanout), per-step retrieval, and an
evidence-preserving merge that records conflicts instead of hiding them.

RLM may only expand HAC handles. It never walks the raw graph freely.

## Token-Budgets (Kontextfenster-Profil)

Eine rekursive Analyse hat neben Tiefe/Fan-out/Schritten Token-Budgets aus dem effektiven Kontextfenster
(`codecompass_rlm_service.rlm_token_budgets`, `docs/context-window-profiles.md`):

- **Synthese**: der Anteil „evidence“ (45 % des Fensters, 32k: 14 745 Token) für alle zusammengeführten Belege;
- **Kind-Retrieval**: Synthese / Fan-out als `max_tokens` jedes Schritts (32k, Fan-out 4: ~3 700).

Ein größeres Fenster lässt jedes Kind mehr mitbringen, fügt aber keine Schritte hinzu. Sind die Belege voll,
endet die Analyse (`trace: evidence_budget`, Warnung `evidence_budget_reached`, Kürzung protokolliert unter
`rlm.evidence`) – nichts wird still abgeschnitten. Der normale Context Planner bleibt für einfache Fragen
bevorzugt (`rlm_is_eligible`).
