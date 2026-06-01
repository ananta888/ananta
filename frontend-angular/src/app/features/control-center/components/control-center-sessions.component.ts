import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { AsyncPipe, DatePipe, NgFor, NgIf } from '@angular/common';
import { CcAgentSession } from '../models/control-center.models';
import { StatusChipComponent } from './status-chip.component';
import { ControlCenterToolTimelineComponent } from './control-center-tool-timeline.component';
import { ControlCenterSecurityInspectorComponent } from './control-center-security-inspector.component';
import { ControlCenterVerificationPanelComponent } from './control-center-verification-panel.component';
import { ControlCenterEventStreamService } from '../services/control-center-event-stream.service';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';

@Component({
  standalone: true,
  selector: 'app-control-center-sessions',
  imports: [NgFor, NgIf, AsyncPipe, DatePipe, StatusChipComponent, ControlCenterToolTimelineComponent, ControlCenterSecurityInspectorComponent, ControlCenterVerificationPanelComponent],
  template: `
    <h2>Sessions</h2>
    <p class="muted">Event Stream: <app-status-chip [label]="(stream.state$ | async) || 'disconnected'" [tone]="streamTone((stream.state$ | async) || 'disconnected')" /></p>
    <p class="muted">Letztes Event: {{ (stream.lastHeartbeatAt$ | async) ? ((stream.lastHeartbeatAt$ | async)! | date:'HH:mm:ss') : 'n/a' }}</p>
    <div class="grid">
      <article class="session" *ngFor="let s of sessions">
        <header>
          <strong>{{ s.id }}</strong>
          <app-status-chip [label]="s.status" [tone]="sessionTone(s.status)" />
        </header>
        <p class="muted">Worker: {{ s.workerId }} ({{ s.workerType }}) · Runtime: {{ s.runtime }} · Modell: {{ s.model }}</p>
        <p class="muted">Policy {{ s.policySnapshot.policyVersion }} · Risk: {{ s.policySnapshot.riskLevel }}</p>
        <app-control-center-tool-timeline [items]="s.toolCalls"></app-control-center-tool-timeline>
        <app-control-center-security-inspector [policy]="s.policySnapshot"></app-control-center-security-inspector>
        <app-control-center-verification-panel [verification]="verificationFor(s.taskId)"></app-control-center-verification-panel>
      </article>
    </div>
    <p *ngIf="!sessions.length" class="muted">Keine Sessions gefunden.</p>
  `,
  styles: [`.grid{display:grid;grid-template-columns:repeat(2,minmax(280px,1fr));gap:10px}.session{border:1px solid #1f2937;border-radius:10px;padding:10px;background:#0f172a}header{display:flex;justify-content:space-between}.muted{color:#94a3b8;font-size:12px}@media (max-width:900px){.grid{grid-template-columns:1fr}}`]
})
export class ControlCenterSessionsComponent implements OnInit, OnDestroy {
  readonly state = inject(ControlCenterStateFacade);
  constructor(public stream: ControlCenterEventStreamService) {}

  sessions: CcAgentSession[] = [];

  ngOnInit(): void {
    this.state.sessions$.subscribe((rows) => {
      this.sessions = rows.map((row) => ({
        id: row.id,
        taskId: row.task_id || null,
        workerId: row.worker_id || row.owner_user_id || 'unknown',
        workerType: this.toWorkerType(row.worker_type),
        model: row.model || 'unknown',
        runtime: this.toRuntime(row.runtime, row.transport),
        status: this.toSessionStatus(row.status),
        updatedAt: new Date().toISOString(),
        policySnapshot: {
          riskLevel: 'medium',
          allowedTools: [],
          deniedTools: [],
          allowedPaths: [],
          deniedPaths: [],
          cloudAllowed: null,
          runtimeBoundary: 'unknown',
          requiresHumanApproval: false,
          approvalReason: 'Policy data not yet bound',
          policyVersion: row.policy_snapshot_id || 'unavailable',
          isPlaceholder: true,
        },
        toolCalls: [],
      }));
      for (const session of this.sessions) {
        if (session.taskId) this.state.loadTaskDetailVerification(session.taskId);
      }
    });
    this.state.loadSessions();
    this.state.connectEvents();
  }

  ngOnDestroy(): void {
    this.state.disconnectEvents();
  }

  sessionTone(s: CcAgentSession['status']): 'neutral'|'ok'|'warn'|'danger'|'info' {
    if (s === 'done') return 'ok';
    if (s === 'failed' || s === 'cancelled') return 'danger';
    if (s === 'blocked' || s === 'waiting_for_approval') return 'warn';
    if (s === 'running') return 'info';
    return 'neutral';
  }

  streamTone(s: string): 'neutral'|'ok'|'warn'|'danger'|'info' {
    if (s === 'connected') return 'ok';
    if (s === 'reconnecting') return 'warn';
    if (s === 'failed') return 'danger';
    if (s === 'connecting') return 'info';
    return 'neutral';
  }

  private toSessionStatus(raw: string): CcAgentSession['status'] {
    const status = String(raw || '').toLowerCase();
    if (status === 'cancelled') return 'cancelled';
    if (status === 'running') return 'running';
    return 'idle';
  }

  private toWorkerType(raw: string | null | undefined): CcAgentSession['workerType'] {
    const value = String(raw || '').trim().toLowerCase();
    if (value === 'ananta-worker' || value === 'opencode' || value === 'hermes' || value === 'codex' || value === 'claude-code') {
      return value;
    }
    return 'custom';
  }

  private toRuntime(runtime: string | null | undefined, transport: string | null | undefined): CcAgentSession['runtime'] {
    const value = String(runtime || '').trim().toLowerCase();
    if (value === 'local' || value === 'docker' || value === 'remote' || value === 'cloud') return value;
    const fallback = String(transport || '').trim().toLowerCase();
    if (fallback === 'hub_relay') return 'remote';
    return 'local';
  }

  verificationFor(taskId: string | null) {
    if (!taskId) return null;
    const entry = this.state.taskVerificationById$.value[taskId];
    if (!entry) return null;
    return {
      status: (entry.status as 'not_run' | 'running' | 'passed' | 'failed' | 'partial' | 'skipped'),
      testCount: entry.test_count,
      passedCount: entry.passed_count,
      failedCount: entry.failed_count,
    };
  }
}
