import { Component, OnInit, OnDestroy } from '@angular/core';
import { NgFor, AsyncPipe } from '@angular/common';
import { CcAgentSession } from '../models/control-center.models';
import { StatusChipComponent } from './status-chip.component';
import { ControlCenterToolTimelineComponent } from './control-center-tool-timeline.component';
import { ControlCenterSecurityInspectorComponent } from './control-center-security-inspector.component';
import { ControlCenterVerificationPanelComponent } from './control-center-verification-panel.component';
import { ControlCenterEventStreamService } from '../services/control-center-event-stream.service';

@Component({
  standalone: true,
  selector: 'app-control-center-sessions',
  imports: [NgFor, AsyncPipe, StatusChipComponent, ControlCenterToolTimelineComponent, ControlCenterSecurityInspectorComponent, ControlCenterVerificationPanelComponent],
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
  `,
  styles: [`.grid{display:grid;grid-template-columns:repeat(2,minmax(280px,1fr));gap:10px}.session{border:1px solid #1f2937;border-radius:10px;padding:10px;background:#0f172a}header{display:flex;justify-content:space-between}.muted{color:#94a3b8;font-size:12px}@media (max-width:900px){.grid{grid-template-columns:1fr}}`]
})
export class ControlCenterSessionsComponent implements OnInit, OnDestroy {
  constructor(public stream: ControlCenterEventStreamService) {}

  sessions: CcAgentSession[] = [{
    id: 'sess-101', taskId: 't1', workerId: 'alpha', workerType: 'ananta-worker', model: 'qwen2.5-coder:7b', runtime: 'docker', status: 'running', updatedAt: new Date().toISOString(),
    policySnapshot: { riskLevel: 'high', allowedTools: ['exec', 'rg'], deniedTools: ['git_push'], allowedPaths: ['/app'], deniedPaths: ['/secrets'], requiresHumanApproval: true, approvalReason: 'git_push requires approval', policyVersion: 'v1' },
    toolCalls: [
      { id:'c1', toolName:'rg', status:'completed', startedAt:null, finishedAt:null },
      { id:'c2', toolName:'exec_command', status:'running', startedAt:null, finishedAt:null },
      { id:'c3', toolName:'git_push', status:'denied', startedAt:null, finishedAt:null }
    ]
  }];

  ngOnInit(): void {
    this.stream.connect('/api/events/stream');
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
}
