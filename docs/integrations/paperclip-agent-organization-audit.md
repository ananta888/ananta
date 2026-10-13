# Paperclip / ai-employees — Source-Audit und Integrationsklassifikation (PAO-001/PAO-002)

Quellcode-Audit (nicht README) der beiden Referenz-Repos gegen Ananta. Ergebnis: eine Mapping-/
Overlap-Matrix mit Klassifikation **adopt / adapt / reference-only / reject**. Grundregel: Ananta
bleibt die **einzige Control Plane** (siehe ADR `docs/decisions/adr-ananta-single-control-plane.md`).

## Audit-Basis (Commit-SHAs)

| Repo | Commit | Datum | Lizenz | Rolle |
|---|---|---|---|---|
| `github.com/paperclipai/paperclip` | `5a6d0d08a747b4fed00b8c68dcd5df469753d4bd` | 2026-10-13 | MIT | Org-Chart, Agent-Lebenszyklus, Heartbeats, Budgets, Governance, Workspaces, portable Templates, Adapter |
| `github.com/markfulton/ai-employees` | `37bfe17d9e0d06e0105eecabfac8335b1bbe07ac` | 2026-10-08 | MIT | Rollenbasierte Employee-Kits, CONTRACT/CAPABILITIES/SCHEDULE/SKILL, Routinen |

Beide MIT: Ideen und Muster dürfen übernommen werden; **kein Code kopiert** — Ananta implementiert
nativ (Python) nach. Wo Muster übernommen werden, ist die Quelle unten benannt.

## Paperclip — konkrete Fundstellen

| Bereich | Upstream-Datei | Inhalt | Ananta-Klassifikation |
|---|---|---|---|
| Org-Einheit | `packages/db/src/schema/companies.ts`, `company_memberships.ts` | Company als Mandant, Mitgliedschaften | **adapt**: Ananta `Organization` über bestehendem Tenant/Project |
| Agent + Hierarchie | `packages/db/src/schema/agents.ts` (`role`, `title`, `reportsTo` self-FK, `lifecycleState`, `budgetMonthlyCents`, `spentMonthlyCents`, `pauseReason`, `permissions`, `lastHeartbeatAt`) | Agent mit Reporting-Linie + Budget + Lifecycle | **adapt** (Rolle ≠ Runtime-Worker; Hierarchie azyklisch) |
| Agent-Mitgliedschaft | `agent_memberships.ts` | User↔Agent | **reference-only** (Ananta hat Memberships) |
| Wakeup | `agent_wakeup_requests.ts` (`source`, `triggerDetail`, `status`, `coalescedCount`, `idempotencyKey`, `runId`) | Koaleszierte, idempotente Wakeups | **adopt** (als Hub-native Wakeup-Requests) |
| Heartbeat-Run | `heartbeat_runs.ts` (`scopeKind` company/issue, `invocationSource`, `executionControlDeadlineAt`, `executionStatusDeliveryId`), `server/src/services/heartbeat.ts`, `heartbeat-policy.ts` | Lauf-Lebenszyklus, Deadline erst nach Execution, opt-in wakeOnDemand | **adopt** (Hub-Runtime) |
| Budget-Policy | `packages/db/src/schema/budget_policies.ts` (`scopeType`/`scopeId`, `metric`, `windowKind`, `amount`, `warnPercent`, `hardStopEnabled`, `unpricedUsagePolicy`, `enforcementVersion`), `server/src/services/budgets.ts`, `budget-reservations.ts`, `budget_incidents.ts`, `cost_events.ts` | Mehrskopige Budgets mit Warn/Hard-Stop/Reservierung/Incidents | **adopt** (Hub-Budgetierung) |
| Approval/Governance | `packages/db/src/schema/approvals.ts`, `issue_approvals.ts`, `server/src/services/approvals.ts` | Approval mit `type`, `payload`, `decidedBy` | **adopt** (an bestehende Approval-Gates binden) |
| Routinen | `packages/db/src/schema/routines.ts` (`concurrencyPolicy`, `catchUpPolicy`, `activityGatePolicy`, `variables`, `latestRevisionId`), `routine_documents.ts` | Revisionierte, planbare Routinen | **adapt** (als Hub-Routinen mit Window-Guard/Period-Key) |
| Skills | `packages/db/src/schema/company_skills.ts` (`markdown`, `trustLevel`, `sharingScope`, `fileInventory`, fork) | Skill-Katalog mit Trust-Level | **adapt** (Ananta-Skills) |
| Workspaces | `execution_workspaces*.ts`, `project_workspaces.ts` | Repo-Roots + Runtime-Leases | **reference-only** (Ananta hat Worker-Container/Workspaces) |
| Adapter/Plugins/Runner | `packages/adapters`, `packages/paperclip-runner`, `plugin_*` | Adapter-Marktplatz | **reference-only** (Ananta `cli_backends` bleibt Owner) |
| Chat/Kanäle, Billing, Marketplace | `chat_*`, `finance_events`, `evals` | | **reject** (nicht Ananta-Scope) |

## ai-employees — konkrete Fundstellen

| Bereich | Upstream-Datei | Inhalt | Ananta-Klassifikation |
|---|---|---|---|
| Employee-Kit | `employees/<role>/CONTRACT.md` | Verbindliche Rolle, Guardrails, „one writer per file" | **adapt** (Rollen-Vertrag, aber über Hub-Policy) |
| Capability↔Tool-Trennung | `employees/<role>/CAPABILITIES.md` | Routinen nennen **Capability-Namen**, nur diese Datei nennt konkrete Tool-/Harness-Routen | **adopt** (Tool-Routing-Policy evolvierbar, Routen separat) |
| Schedule | `employees/<role>/SCHEDULE.md` (`days`, `fire`, `window_start`, `window_end`, `key`, `budget`, `browser`) | Machine-lesbare Routine-Zeilen; Window-Guard + Period-Key | **adopt** (Hub-Routinen-Scheduler) |
| Routine | `employees/<role>/routines/<id>/SKILL.md` | Eine Aufgabe je Routine, keine Uhrzeit im Body | **adapt** |
| Release/Guardrails | `CONTRACT.md` §7, `RELEASES.md` | Zwei Guardrails: Kanal-Freigabe + immer-an | **adapt** (Hub-Approval/Policy) |

## Overlap-Matrix gegen bestehende Ananta-Dienste

| Funktion | Ananta-Owner (heute) | Upstream | Entscheidung |
|---|---|---|---|
| Task-Erzeugung/State-Machine | `task_orchestration_service`, `task_state_machine_service`, `task_runtime_service` | paperclip issues/`issue_*` | **Ananta bleibt Owner**; keine zweite State-Machine |
| Delegation/Claim | `task_delegation_services`, `task_claim_service`, `native_graph_delegation_service` | `reportsTo`, Agent-Hire | **Ananta bleibt Owner**; Hierarchy nur als Policy-Kontext |
| Budget/Kosten | `workflow_runtime` Budget-Reservierungen, `cost_aggregation_service` | `budget_policies`/`budgets.ts` | **adapt**: mehrskopige Policies ergänzen, Enforcement bleibt Hub |
| Approval/Policy | `hitl_gate_service`, `mutation_gate_service`, `hub_evidence_gate_service` | `approvals.ts` | **adapt**: Org-Aktionen an bestehende Gates binden |
| Heartbeat/Schedule | (Background-Services, Autopilot-Ticks) | `heartbeat_runs`, `routines` | **adapt**: Hub-native Routine-/Wakeup-Runtime |
| Worker-Registrierung | `agent_registry_service`, `pi_runtime_*` | `agents`, `adapters` | **Ananta bleibt Owner** |
| Memory/CodeCompass/Skills | `memory_*`, `codecompass_*`, Skills-Kataloge | `company_skills` | **adapt** |
| Workspaces/Container | Worker-Container-Modell (AGENTS.md) | `execution_workspaces` | **Ananta bleibt Owner** |

**Doppelte Task-/State-Machines werden explizit ausgeschlossen.** Kein Upstream-Konzept wird als
eigene Orchestrierung importiert; alle Org-Konzepte sind **Policy-Kontext** über der bestehenden
Hub-Task-Queue.

## Migrations- und Kompatibilitätsrisiken

1. **Autoritätsverlust**: Org-Hierarchie darf nie Worker-zu-Worker-Autorität erzeugen → PAO-004
   erzwingt Hub-only-Delegation.
2. **Budget-Drift**: Mehrskopige Budgets müssen in die bestehende Reservierungs-/CAS-Logik passen,
   sonst doppelte Seiteneffekte.
3. **Scheduler-Duplikat**: Heartbeats dürfen den bestehenden Autopilot-Tick nicht duplizieren.
4. **Skill-/Tool-Kopplung**: Capability↔Route-Trennung verhindert, dass Org-Rollen Tool-Rechte
   implizit erweitern.
5. **Rechte aus Rollennamen**: verboten; Rechte ausschließlich über Policy-/Capability-Verträge
   (siehe PAO-003).

## Bereits vorhandene Ananta-Deckung (kein Neubau)

Wichtig für PAO-003: Der Großteil des Domänenmodells existiert bereits und wird **wiederverwendet**,
nicht dupliziert:

| Paperclip-Konzept | Vorhandenes Ananta-Äquivalent |
|---|---|
| Company/Organization | `OrganizationInstanceDB`, `OrganizationUnitDB` (`agent/db_models/organization_topology.py`) |
| Reporting-Linie | `OrganizationUnitDB` Parent-Link + ADR-enterprise-organization-control-plane |
| Rolle | `RoleTemplateRevisionDB`, `OrganizationRoleSlotDB` |
| AgentAssignment | `OrganizationRoleAssignmentDB` (Lifecycle proposed/active/suspended/ended) |
| Membership | `OrganizationMembershipDB` |
| CapabilityBoundary | `OrganizationPolicyRevisionDB` + Policy-/Capability-Verträge (nie aus Rollennamen) |
| Budgets | `OrganizationBudgetUsageDB`/`OrganizationBudgetReservationDB`, `organization_budget_service` |
| Approval/Governance | `hitl_gate_service`, `mutation_gate_service`, `ApprovalRequestDB` |
| Hierarchische Delegation (Hub-only) | ADR §6 (Worker reicht nur geschlossene Proposal ein), `native_graph_delegation_service` |

Die **additiven, paperclip-/ai-employees-inspirierten** Lücken, die neu implementiert werden:

- `ananta_contracts/agent_routine.py` — Routine-Schedule-Zeile mit `days`-Vokabular, Window-Guard,
  Period-Key und Budget (aus `SCHEDULE.md`).
- `agent/services/agent_wakeup.py` — koaleszierte, idempotente Hub-Wakeup-Requests (aus
  `agent_wakeup_requests.ts`).
- `agent/services/agent_delegation_policy.py` — Hub-only Chain-of-Command-Validierung (PAO-004).
- Capability↔Route-Trennung für Tool-Routing (aus `CAPABILITIES.md`) als Policy-Kontext.
