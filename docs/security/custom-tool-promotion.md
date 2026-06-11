# Security: Custom-Tool-Promotion

Track: `todos/todo.hub-direct-execution-dynamic-tools.json`
(HDE-015..HDE-019), Amendment HDW-003/HDW-004.

## Invarianten

1. **LLMs dürfen Tools vorschlagen, aber niemals registrieren,
   aktivieren oder direkt ausführen.** Proposals sind inerte
   Artefakte; jede Aktivierung läuft über Schema-Validation,
   Test-Validation und digest-gebundenes Approval (oder einen explizit
   auditierten Admin-Override).
2. **Unrestricted Shell bleibt verboten.** Custom Tools bestehen aus
   Token-Templates mit typisierten Argumenten oder Scripts aus dem
   genehmigten Store; das final gerenderte Kommando durchläuft den
   `ShellCommandAnalyzer`; `subprocess` läuft mit `shell=False`.
   Script-Tools sind zusätzlich an `script_body_digest` gebunden; jede
   Änderung am Script-Inhalt nach Proposal/Validation/Approval blockt
   die Ausführung.
3. **Der Hub führt nicht aus.** Custom Tools laufen über den
   `WorkerRuntimeExecutionAdapter` im `CustomToolExecutor`
   (execution_plane `worker_runtime`/`sandbox_runtime`), nie im
   Hub-Prozess (HDW-DD-001).
4. **Default-deaktiviert.** Neue Tools entstehen `pending`; nur
   `status=active` + `approval_status=granted` Tools sind ausführbar,
   sichtbar im Prompt oder im HubDirectRouter wählbar.

## Statusmaschine (HDE-015)

```
pending --validate--> validated ----request_approval----> approval_required
   |                     |                                     |
   |                validation_failed                     granted -> approved --activate--> active
   |                                                      denied  -> rejected      |
   +--reject--> rejected                                              disabled <----+--> rollback (nur auf
                                                                                     validierte+approved Version)
```

- `validate` läuft die Proposal-Tests in einem isolierten
  Temp-Workspace (mind. 1 positiver + 1 negativer Fall, sonst keine
  Validation) und speichert den Report als Artefakt.
- `request_approval` erzeugt einen `ApprovalRequest` mit
  `target_fingerprint=proposal_digest` — ein geänderter Vorschlag hat
  einen neuen Digest und kann alte Grants nicht wiederverwenden.
- `activate` prüft `validated_digest == proposal_digest` und
  `approval_status == granted`. Admin-Override ist möglich, erfordert
  aber weiterhin eine bestandene Validation und wird auditiert.

## Laufzeitgrenzen (HDE-016/HDE-018, HDW-004)

- Timeout (`timeout_seconds`), Output-Cap (`output_max_chars`),
  `cwd` im Workspace, `allowed_paths`/`denied_paths` für
  Pfad-Argumente, Env nur aus `env_allowlist` + minimaler Basis
  (`PATH`, `HOME`, …).
- MutationGate: Workspace-Baseline vor/nach jeder Ausführung.
  `read_only`-Tools mit Dateiänderungen ⇒ `blocked` +
  `workspace_mutation_blocked`. `controlled_write` darf nur deklarierte
  Pfade ändern; alles andere blockt das Ergebnis. Die kanonischen
  Audit-Events bleiben `workspace_baseline_created`,
  `workspace_mutation_evaluated`, `workspace_mutation_blocked`.
  Wenn die Baseline wegen File-Limit oder Lesefehlern unvollständig ist,
  blockt der Executor fail-closed mit `workspace_baseline_incomplete`;
  unvollständige Snapshots dürfen keine read-only Ergebnisse erlauben.
- Argumente: nur deklarierte Properties, Typprüfung, keine
  Shell-Metazeichen in Werten, Pfad-Argumente nur innerhalb des
  Workspace.

## Sichtbarkeit & Rollen (HDE-013/HDE-022)

- Prompt-Beschreibungen (`describe_for_prompt(include_dynamic=True)`)
  und Registry-Snapshots enthalten Name, Zweck, Argumente und Risiko —
  keine Script-Interna, keine Secrets.
- Pending/rejected/disabled Tools erscheinen weder im Prompt noch im
  Router noch im Executor.
- Promotion-Aktionen (validate/approve/activate/disable/rollback) sind
  Admin-only (`admin_required`); Proposal-Erstellung steht
  authentifizierten Nutzern/LLM-Surfaces offen, bleibt aber inert.
- Proposal- und Registry-JSON-Dateien werden atomar geschrieben
  (temp-file + rename); Disable/Rollback/Usage-Updates behalten Version
  History und Status konsistent.
