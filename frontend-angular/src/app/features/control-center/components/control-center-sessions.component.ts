import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { AsyncPipe, NgFor, NgIf } from '@angular/common';
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
  imports: [NgFor, NgIf, AsyncPipe, StatusChipComponent, ControlCenterToolTimelineComponent, ControlCenterSecurityInspectorComponent, ControlCenterVerificationPanelComponent],
  template: `
    <h2>Sessions</h2>
    <p class="muted">Event Stream: <app-status-chip [label]="(stream.state$ | async) || 'disconnected'" [tone]="streamTone((stream.state$ | async) || 'disconnected')" /></p>
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
        <app-control-center-verification-panel [verification]="{status:'running',testCount:12,passedCount:6,failedCount:0}"></app-control-center-verification-panel>
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
        workerId: row.owner_user_id || 'unknown',
        workerType: 'ananta-worker',
        model: 'n/a',
        runtime: row.transport === 'hub_relay' ? 'remote' : 'local',
        status: this.toSessionStatus(row.status),
        updatedAt: new Date().toISOString(),
        policySnapshot: {
          riskLevel: 'medium',
          allowedTools: [],
          deniedTools: [],
          allowedPaths: ['/'],
          deniedPaths: [],
          requiresHumanApproval: false,
          approvalReason: null,
          policyVersion: 'v1',
        },
        toolCalls: [],
      }));
    });
    this.state.loadSessions();
    const base = this.state.hubBaseUrl();
    if (base) this.stream.connect(`${base}/api/events/stream`);
  }

  ngOnDestroy(): void {
    this.stream.disconnect();
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
}
