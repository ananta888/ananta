import { Injectable, WritableSignal, inject, signal } from '@angular/core';

import {
  DryRunResult,
  ValidationResult,
  VisualProcessApiService,
  VpGraph,
  WorkflowStatus,
  VpRuntimeOverlay,
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
  readonly runtimeOverlay = signal<VpRuntimeOverlay | null>(null);
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
        this.startPolling();
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

  attach(workflowId: string): void {
    this.activeWorkflowId.set(workflowId);
    this.startPolling();
    this.refresh();
  }

  detach(): void { this.stopPolling(); this.activeWorkflowId.set(null); }

  refresh(): void {
    const workflowId = this.activeWorkflowId();
    if (!workflowId) return;
    this.api.getWorkflowStatus(workflowId).subscribe({ next: status => this.applyStatus(status), error: () => this.status.set('Status konnte nicht geladen werden') });
  }

  signalGate(action: 'approve' | 'reject', stepId: string | null): void {
    const workflowId = this.activeWorkflowId();
    if (!workflowId || !stepId) return;
    this.api.signalWorkflow(workflowId, action, { step_id: stepId }).subscribe({
      next: () => this.status.set(action === 'approve' ? 'Gate genehmigt ✓' : 'Gate abgelehnt'),
      error: error => this.status.set(`Gate-Fehler: ${error?.error?.detail ?? 'unbekannt'}`),
    });
  }

  private startPolling(): void {
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
      this.api.getWorkflowStatus(workflowId).subscribe(status => this.applyStatus(status));
    }, POLL_INTERVAL_MS);
  }

  private applyStatus(status: WorkflowStatus): void {
    this.workflowStatus.set(status);
    const steps = (status['steps'] as any[] | undefined) ?? [];
    const normalize = (value: string): VpRuntimeOverlay['steps'][string]['status'] => {
      const mapped: Record<string, VpRuntimeOverlay['steps'][string]['status']> = { done:'succeeded', success:'succeeded', waiting:'pending', cancelled:'cancelled', canceled:'cancelled' };
      const candidate = mapped[value] ?? value;
      return ['pending','running','awaiting_approval','succeeded','failed','skipped','cancelled'].includes(candidate) ? candidate as VpRuntimeOverlay['steps'][string]['status'] : 'unknown';
    };
    const mappedSteps: VpRuntimeOverlay['steps'] = Object.fromEntries(steps.filter(item => item?.step_id).map(item => [item.step_id, {
      step_id: item.step_id, status: normalize(item.run_state ?? item.status ?? 'unknown'),
      started_at: item.started_at, finished_at: item.finished_at, duration_ms: item.duration_ms, error: item.error,
      gate: item.gate, selected_model_profile_id: item.selected_model_profile_id, selected_provider_id: item.selected_provider_id,
      selected_model: item.selected_model, fallback_attempts: item.fallback_attempts ?? [], llm_call_profile: item.llm_call_profile ?? [],
    }]));
    this.runtimeOverlay.set({
      run_id: String(status['run_id'] ?? status.workflow_id), workflow_id: status.workflow_id,
      process_id: status['process_id'] as string | undefined, process_version: status['process_version'] as string | undefined,
      snapshot_hash: status['snapshot_hash'] as string | undefined, overall_status: status.status,
      current_step_ids: Object.values(mappedSteps).filter(step => ['running','awaiting_approval'].includes(step.status)).map(step => step.step_id),
      steps: mappedSteps, started_at: status['started_at'] as number | undefined, finished_at: status['finished_at'] as number | undefined,
      updated_at: Date.now(), error: status['error'] as string | undefined, gate: status['gate'] as Record<string, unknown> | undefined,
    });
    if (['done', 'failed', 'cancelled'].includes(status.status)) {
      this.stopPolling();
      this.status.set(status.status === 'done' ? 'Workflow abgeschlossen ✓' : `Workflow ${status.status}`);
    }
  }

  private stopPolling(): void {
    if (this.pollHandle !== null) clearInterval(this.pollHandle);
    this.pollHandle = null;
  }
}
