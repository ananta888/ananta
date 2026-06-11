# ananta-worker Workspace Patch Iteration

Track: `todos/todo.ananta-worker-workspace-patch-iteration.json` (AWWPI).
Verträge: `docs/contracts/ananta-worker-mutation-mode.md`,
`docs/contracts/ananta-worker-workspace-patch.md`.
Security: `docs/security/ananta-worker-workspace-mutation-policy.md`.
Rollout: `docs/release/ananta-worker-workspace-patch-rollout.md`.

## OpenCode vs. ananta-worker (AWWPI-001)

**OpenCode** kann bereits direkt im Workspace arbeiten: der Hub bereitet
Kontextdateien vor (`prepare_opencode_context_files` — AGENTS.md,
`.ananta/task-brief.md`, Response-Contract), erstellt eine **Baseline**,
lässt den Agenten laufen und sammelt danach **Diffs und geänderte Dateien
als Artefakte** ein (`create_workspace_diff_artifact`,
`sync_changed_files_to_artifacts`). Diese Bausteine existierten vor dem
Track und bleiben unverändert.

Der **ananta-worker** war dagegen primär ein Kontext-Batch-/Analyse-Loop
(`_run_ananta_worker_iterative`): CodeCompass-Batches → progress.md →
Synthese. Dateien änderte er nicht belastbar.

**Warum patch-first?** Brownfield-Codefixes sollen gezielt gesucht,
range-basiert gelesen und als kleine Patches angewendet werden. Deshalb ist
`strict_patch_request` der bevorzugte Standard für Coding/Bugfix/Refactor.
`controlled_workspace` bleibt für kleine, explizit materialisierte
Kompatibilitätsfälle verfügbar.

## Batch-Iteration vs. Feedback-Iteration (AWWPI-021)

**Bestehende Batch-Iteration** (bleibt als Fallback erhalten):

```
Kontext-Batch 1 → Analyse → progress.md
Kontext-Batch 2 → Analyse → progress.md
…
finale Synthese
```

Das ist *Daten*-Iteration: dieselbe Aufgabe über mehrere Kontextfenster.

**Neue Feedback-Iteration** (`agent/common/sgpt_workspace_mutation.py`):

```
Aktion (workspace_write / patch_request / test.run)
  → Hub-Beobachtung: DiffResult + PolicyResult (+ TestResult, ContextReloadResult)
    → Evidence komprimiert & dedupliziert in den nächsten Prompt
      → nächste gezielte Aktion
```

Das ist *Verhaltens*-Iteration: der Worker reagiert auf die Prüfung seiner
letzten Aktion statt nur eine finale Synthese aus altem Fortschritt zu
schreiben (AWWPI-DD-006).

### Bugfix-Beispiel mit fehlschlagendem Test

1. **Iteration 1:** Worker ändert `app.py` (workspace_write) oder fordert
   `test.run` an. Hub erzeugt DiffResult + PolicyResult; der Test schlägt
   fehl → TestResult `{rc: 1, stderr_excerpt: "AssertionError …"}`.
2. **Iteration 2:** Worker sieht TestResult/Policy-Warnung als Evidence und
   verbessert **gezielt** die betroffene Stelle. Hub prüft erneut.
3. **Iteration 3:** Diff/Tests/Policy akzeptabel → `final_answer`.
   Der Hub akzeptiert das final_answer nur, wenn der letzte PolicyResult
   `ok` ist — sonst wird das Ergebnis als `policy_blocked` markiert.

(Abgedeckt durch
`tests/test_ananta_worker_workspace_feedback_iteration.py::test_failing_test_triggers_second_targeted_iteration`.)

## Loop-Endbedingungen & NoProgressDetection

| Endbedingung | Auslöser |
|---|---|
| `final_answer` | Modell beendet; nur bei akzeptablem PolicyResult Erfolg |
| `max_iterations` | `max_feedback_iterations` erreicht |
| `max_patch_attempts_per_file` | zu viele Versuche an derselben Datei |
| `policy_blocked` | finale Policy-Verletzung |
| `approval_required` | Patch-Tool braucht Hub-Approval |
| `invalid_output_limit_reached` | wiederholt ungültiges JSON |
| `no_progress_detected` | identische Änderungssignatur ohne Verbesserung |

**NoProgressDetection:** Nach jeder mutierenden Aktion wird eine Signatur
aus (geänderte Pfade + Content-Hashes) gebildet. Wiederholt sich die
Signatur (dieselbe Stelle ohne effektive Änderung), endet der Loop
kontrolliert mit `no_progress_detected`.

## Runtime-Flow (Soll, implementiert)

1. Hub wählt `mutation_mode` (task_kind, risk, explizite Config — siehe
   Mutation-Mode-Contract).
2. Hub erstellt/reused `WorkerWorkspaceContext`.
3. Hub materialisiert nur erlaubte Dateien
   (`materialize_allowed_workspace_files` → Manifest mit source,
   workspace_path, hash, allowed_operations).
4. Hub schreibt AGENTS.md, `.ananta/task-brief.md`,
   `.ananta/response-contract.md` (modusspezifisch:
   `prepare_ananta_worker_context_files`) und optional CodeCompass-Kontext.
5. Baseline vor mutierender Ausführung (`refresh_mutation_baseline`).
6. Loop gemäß Modus (read_only analysiert nur; controlled_workspace
   schreibt direkt im Rahmen; strict_patch_request über Hub-Patches).
7. Nach jeder Aktion: DiffResult + PolicyResult (+ TestResult), als
   Evidence in den Folgeprompt.
8. Finale meaningful changed files + `workspace.diff` werden als Artefakte
   synchronisiert — **oder bei Policy-Verletzung blockiert**
   (Mutation-Report-Filter im Sync-Pfad).

## Diagnostik

`.ananta/mutation-report.json` pro Lauf (mutation_mode, Iterationen mit
Policy-Entscheidungen, final_policy_result, Outcome) — UI-Seite
„Worker Loop Diagnostik" unterscheidet Batch-Läufe (kein Report) von
Feedback-Läufen und zeigt geblockte Änderungen mit Begründung.
