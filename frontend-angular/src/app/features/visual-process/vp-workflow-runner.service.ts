import { Injectable, WritableSignal, inject, signal } from '@angular/core';

import {
  DryRunResult,
  ValidationResult,
  VisualProcessApiService,
  VpGraph,
  WorkflowStatus,
} from './visual-process-api.service';

const POLL_INTERVAL_MS = 3000;
const POLL_MAX_MS = 10 * 60 * 1000;

@Injectable()
export class VpWorkflowRunnerService {
  private readonly api = inject(VisualProcessApiService);
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private pollStartedAt = 0;

  readonly validationResult = signal<ValidationResult | null>(null);
  readonly dryRunResult = signal<DryRunResult | null>(null);
  readonly activeWorkflowId = signal<string | null>(null);
  readonly workflowStatus = signal<WorkflowStatus | null>(null);
  readonly status = signal('');

  destroy(): void { this.stopPolling(); }

  validate(graph: VpGraph): void {
    this.api.validate(graph).subscribe({
      next: result => {
        this.validationResult.set(result);
        this.status.set(result.valid ? 'Gültig ✓' : `${result.error_count} Fehler`);
      },
      error: () => this.status.set('Validierung fehlgeschlagen'),
    });
  }

  dryRun(graph: VpGraph): void {
    this.status.set('Dry-Run läuft…');
    this.api.dryRun(graph).subscribe({
      next: result => {
        this.dryRunResult.set(result);
        this.validationResult.set(result.validation);
        this.status.set('Dry-Run abgeschlossen');
      },
      error: () => this.status.set('Dry-Run fehlgeschlagen'),
    });
  }

  saveAsBlueprint(graph: VpGraph): void {
    this.api.saveAsBlueprint(graph).subscribe({
      next: result => this.status.set(`Blueprint gespeichert (id: ${result.blueprint_id})`),
      error: error => this.status.set(`Blueprint-Fehler: ${error?.error?.detail ?? 'unbekannt'}`),
    });
  }

  refreshPolicyHints(graph: WritableSignal<VpGraph>): void {
    this.api.policySummary(graph()).subscribe({
      next: result => graph.update(current => ({
        ...current,
        steps: current.steps.map(step => ({
          ...step,
          policy_hints: result.per_step[step.id] ?? step.policy_hints,
        })),
      })),
      error: () => undefined,
    });
  }

  start(graph: WritableSignal<VpGraph>): void {
    this.api.startWorkflowFromGraph(graph()).subscribe({
      next: status => {
        this.activeWorkflowId.set(status.workflow_id);
        this.workflowStatus.set(status);
        this.status.set(`Workflow gestartet (id: ${status.workflow_id})`);
        this.startPolling(graph);
      },
      error: error => this.status.set(`Fehler: ${error?.error?.detail ?? 'Workflow konnte nicht gestartet werden'}`),
    });
  }

  cancel(): void {
    const workflowId = this.activeWorkflowId();
    if (!workflowId) return;
    this.api.cancelWorkflow(workflowId).subscribe({
      next: () => {
        this.stopPolling();
        this.status.set('Workflow abgebrochen');
      },
      error: () => this.status.set('Abbrechen fehlgeschlagen'),
    });
  }

  signalGate(action: 'approve' | 'reject', stepId: string | null): void {
    const workflowId = this.activeWorkflowId();
    if (!workflowId || !stepId) return;
    this.api.signalWorkflow(workflowId, action, { step_id: stepId }).subscribe({
      next: () => this.status.set(action === 'approve' ? 'Gate genehmigt ✓' : 'Gate abgelehnt'),
      error: error => this.status.set(`Gate-Fehler: ${error?.error?.detail ?? 'unbekannt'}`),
    });
  }

  private startPolling(graph: WritableSignal<VpGraph>): void {
    this.stopPolling();
    this.pollStartedAt = Date.now();
    this.pollHandle = setInterval(() => {
      const workflowId = this.activeWorkflowId();
      if (!workflowId) return this.stopPolling();
      if (Date.now() - this.pollStartedAt > POLL_MAX_MS) {
        this.stopPolling();
        this.status.set('Polling-Timeout (10 min) — Workflow-Status unbekannt');
        return;
      }
      this.api.getWorkflowStatus(workflowId).subscribe(status => {
        this.workflowStatus.set(status);
        const steps = status['steps'] as any[] | undefined;
        if (steps?.length) {
          graph.update(current => ({
            ...current,
            steps: current.steps.map(step => {
              const runtimeStep = steps.find(item => item.step_id === step.id);
              return runtimeStep ? { ...step, run_state: runtimeStep.run_state } : step;
            }),
          }));
        }
        if (['done', 'failed', 'cancelled'].includes(status.status)) {
          this.stopPolling();
          this.activeWorkflowId.set(null);
          this.status.set(status.status === 'done' ? 'Workflow abgeschlossen ✓' : `Workflow ${status.status}`);
        }
      });
    }, POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollHandle !== null) clearInterval(this.pollHandle);
    this.pollHandle = null;
  }
}
