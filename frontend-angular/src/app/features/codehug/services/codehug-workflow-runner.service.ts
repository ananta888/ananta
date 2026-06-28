import { Injectable, inject, signal } from '@angular/core';
import { Subscription, interval } from 'rxjs';

import { CanvasNode } from '../components/codehug-canvas-types';
import { InternalsService, VpGraph } from './internals.service';

@Injectable()
export class CodehugWorkflowRunnerService {
  private readonly service = inject(InternalsService);
  private polling: Subscription | null = null;

  readonly workflowId = signal<string | null>(null);
  readonly workflowStatus = signal<Record<string, unknown> | null>(null);
  readonly workflowEvents = signal<Record<string, unknown>[]>([]);
  readonly dryRunResult = signal<string | null>(null);
  readonly detRunResult = signal<Record<string, unknown> | null>(null);
  readonly detRunning = signal(false);
  readonly goalResult = signal<string | null>(null);
  readonly goalOk = signal(false);

  destroy(): void { this.polling?.unsubscribe(); }

  runDetStep(node: CanvasNode): void {
    if (!node.detCommand) return;
    this.detRunning.set(true);
    this.detRunResult.set(null);
    this.service.runDetStep(node.detSubtype ?? 'script', node.detCommand, node.detExpectedResult ?? '')
      .subscribe(result => {
        this.detRunResult.set(result);
        this.detRunning.set(false);
      });
  }

  dryRun(graph: VpGraph): void {
    this.service.dryRunVpGraph(graph).subscribe(result => {
      const validation = result?.validation;
      if (!validation) {
        this.goalResult.set('Dry-run: keine Antwort');
        this.goalOk.set(false);
      } else if (validation.valid) {
        this.goalOk.set(true);
        this.goalResult.set(`✓ Valide (${result.step_count} Schritte, ${result.edge_count} Kanten)`);
      } else {
        this.goalOk.set(false);
        this.goalResult.set(`✗ Fehler: ${validation.errors?.join(', ') ?? 'unbekannt'}`);
      }
    });
  }

  start(graph: VpGraph): void {
    this.service.startVpWorkflow(graph, {
      requested_by: 'codehug_internals',
      workflow_type: 'visual_process',
    }).subscribe(result => {
      const workflowId = (result as any)?.workflow_id ?? (result as any)?.id ?? null;
      if (workflowId) {
        this.workflowId.set(workflowId);
        this.startPolling(workflowId);
        this.goalOk.set(true);
        this.goalResult.set(`Workflow gestartet: ${workflowId}`);
      } else {
        this.goalOk.set(Boolean((result as any)?.status && (result as any)?.status !== 'error'));
        this.goalResult.set((result as any)?.status ?? 'Gestartet');
      }
    });
  }

  submitClassicGoal(
    description: string,
    config: { securityLevel: string; blueprint: string; playbook: string },
  ): void {
    fetch('http://127.0.0.1:5000/goals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        description,
        security_level: config.securityLevel,
        config_profile: config.blueprint,
        playbook: config.playbook,
      }),
    }).then(async response => {
      this.goalOk.set(response.ok);
      this.goalResult.set(response.ok ? 'Ziel gesendet.' : `Fehler ${response.status}: ${(await response.text()).slice(0, 80)}`);
    }).catch(error => {
      this.goalOk.set(false);
      this.goalResult.set(`Netzwerkfehler: ${error}`);
    });
  }

  activeStepId(): string | null {
    const steps = Array.isArray(this.workflowStatus()?.['steps'])
      ? this.workflowStatus()?.['steps'] as Record<string, unknown>[]
      : [];
    const active = steps.find(step => ['running', 'waiting_for_approval'].includes(String(step['status'] ?? '')));
    return String(active?.['step_id'] ?? active?.['id'] ?? '') || null;
  }

  private startPolling(workflowId: string): void {
    this.polling?.unsubscribe();
    const load = () => {
      this.service.getVpWorkflowStatus(workflowId).subscribe(status => this.workflowStatus.set(status));
      this.service.getVpWorkflowEvents(workflowId).subscribe(events => this.workflowEvents.set(events));
    };
    load();
    this.polling = interval(2000).subscribe(load);
  }
}
