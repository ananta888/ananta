# Worker Invariants Checklist

Diese Checkliste ist fuer alle execution-faehigen Worker-Pfade verpflichtend (`patch_apply`, `command_execute`, `test_run`, `verify`).

## Ingress / Egress Contract

- [ ] Ingress-Artefakte sind gegen das erwartete Schema validiert.
- [ ] Egress-Artefakte sind vor Rueckgabe validiert.
- [ ] Schema-Fehler werden als expliziter degraded-state (`schema_invalid`) gemeldet.

## Policy / Approval

- [ ] Command-Klassifikation mit Risiko-Klasse ist aktiv.
- [ ] Deny-Entscheidungen werden nie als Erfolg maskiert.
- [ ] Approval-Bindings pruefen mindestens `task_id`, `capability_id`, `context_hash` und Hash-Bindung.
- [ ] Guarded Roots bleiben approval-gebunden.

## Runtime Budgets

- [ ] Profile-Budgets (`safe` / `balanced` / `fast`) sind wirksam.
- [ ] Budget-Exhaustion fuehrt zu explizitem `stop_reason`.
- [ ] Keine unbounded Iterations- oder Runtime-Pfade.

## Traceability

- [ ] Resultate enthalten `trace_id`, `task_id`, `capability_id`, `context_hash`, `policy_decision_ref`.
- [ ] Profile-Info (`worker_profile`, `profile_source`) ist in Routing/Execution sichtbar.
- [ ] Degraded-Reasons sind maschinenlesbar und stabil.
