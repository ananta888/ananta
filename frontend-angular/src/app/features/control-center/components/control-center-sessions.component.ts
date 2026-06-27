import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { AsyncPipe, DatePipe } from '@angular/common';
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
  imports: [AsyncPipe, DatePipe, StatusChipComponent, ControlCenterToolTimelineComponent, ControlCenterSecurityInspectorComponent, ControlCenterVerificationPanelComponent],
  template: `
    <h2>Sessions</h2>
    <p class="muted">Event Stream: <app-status-chip [label]="(stream.state$ | async) || 'disconnected'" [tone]="streamTone((stream.state$ | async) || 'disconnected')" /></p>
    <p class="muted">Letztes Event: {{ (stream.lastHeartbeatAt$ | async) ? ((stream.lastHeartbeatAt$ | async)! | date:'HH:mm:ss') : 'n/a' }}</p>
    <div class="grid">
      @for (s of sessions; track s) {
        <article class="session">
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
      }
    </div>
    @if (!sessions.length) {
      <p class="muted">Keine Sessions gefunden.</p>
    }
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
        policySnapshot: row.policy_snapshot ? {
          riskLevel: this.toRiskLevel(row.policy_snapshot.risk_level),
          allowedTools: row.policy_snapshot.allowed_tools || [],
          deniedTools: row.policy_snapshot.denied_tools || [],
          allowedPaths: row.policy_snapshot.allowed_paths || [],
          deniedPaths: row.policy_snapshot.denied_paths || [],
          cloudAllowed: row.policy_snapshot.cloud_allowed,
          runtimeBoundary: this.toRuntimeBoundary(row.policy_snapshot.runtime_boundary),
          requiresHumanApproval: !!row.policy_snapshot.requires_human_approval,
          approvalReason: row.policy_snapshot.approval_reason || null,
          policyVersion: row.policy_snapshot.policy_version || row.policy_snapshot_id || 'v1',
        } : {
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
        toolCalls: (this.state.toolCallsBySessionId$.value[row.id] || []).map((tc) => ({
          id: tc.id,
          toolName: tc.tool_name || 'unknown',
          status: this.toToolCallStatus(tc.status),
          startedAt: tc.started_at ? new Date(Number(tc.started_at) * 1000).toISOString() : null,
          finishedAt: tc.finished_at ? new Date(Number(tc.finished_at) * 1000).toISOString() : null,
        })),
      }));
      for (const session of this.sessions) {
        if (session.taskId) this.state.loadTaskDetailVerification(session.taskId);
        this.state.loadSessionToolCalls(session.id);
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
    if (status === 'idle' || status === 'proposed' || status === 'running' || status === 'waiting_for_approval' || status === 'blocked' || status === 'review' || status === 'done' || status === 'failed' || status === 'cancelled') return status;
    if (status === 'verified') return 'review';
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

  private toRiskLevel(raw: string | null | undefined): 'low' | 'medium' | 'high' | 'critical' {
    const value = String(raw || '').trim().toLowerCase();
    if (value === 'low' || value === 'medium' || value === 'high' || value === 'critical') return value;
    return 'medium';
  }

  private toRuntimeBoundary(raw: string | null | undefined): 'local-only' | 'cloud-allowed' | 'remote' | 'unknown' {
    const value = String(raw || '').trim().toLowerCase();
    if (value === 'local-only' || value === 'cloud-allowed' || value === 'remote' || value === 'unknown') return value;
    return 'unknown';
  }

  private toToolCallStatus(raw: string): 'proposed' | 'allowed' | 'denied' | 'running' | 'completed' | 'failed' {
    const v = String(raw || '').trim().toLowerCase();
    if (v === 'allowed' || v === 'denied' || v === 'running' || v === 'completed' || v === 'failed' || v === 'proposed') {
      return v;
    }
    if (v === 'require_approval') return 'proposed';
    return 'proposed';
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
