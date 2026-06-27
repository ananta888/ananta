import { Component, OnInit, inject } from '@angular/core';
import { AsyncPipe } from '@angular/common';
import { CcTaskCard, CcTaskStatus } from '../models/control-center.models';
import { StatusChipComponent } from './status-chip.component';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';

@Component({
  standalone: true,
  selector: 'app-control-center-task-board',
  imports: [StatusChipComponent, AsyncPipe],
  template: `
    <h2>Task Board</h2>
    @if (state.loading$ | async) {
      <p class="muted">Lade Tasks ...</p>
    }
    <div class="board">
      @for (col of cols; track col) {
        <section class="col">
          <h3>{{ col }}</h3>
          @for (t of byStatus(col); track t) {
            <article class="card">
              <strong>{{ t.title }}</strong>
              <div class="meta">
                <app-status-chip [label]="t.riskLevel" [tone]="riskTone(t.riskLevel)" />
                <app-status-chip [label]="t.verificationSummary?.status || 'not_run'" [tone]="verificationTone(t.verificationSummary?.status || 'not_run')" />
              </div>
              <div class="muted">Worker: {{ t.assignedWorkerId || 'n/a' }} · Modell: {{ t.preferredModel || 'n/a' }}</div>
            </article>
          }
          @if (!byStatus(col).length) {
            <p class="muted">Keine Eintraege</p>
          }
        </section>
      }
    </div>
    @if (!(tasks.length) && (state.loading$ | async) === false) {
      <p class="muted">Keine Tasks im ausgewaehlten Projekt.</p>
    }
    `,
  styles: [`
    .board{display:grid; grid-template-columns: repeat(4,minmax(180px,1fr)); gap:10px;}
    .col{border:1px dashed #334155; border-radius:10px; padding:8px; background:#0f172a;}
    .card{border:1px solid #1f2937; border-radius:8px; padding:8px; margin-bottom:8px; background:#111827;}
    .meta{display:flex; gap:6px; margin:6px 0;}
    .muted{color:#94a3b8; font-size:12px;}
    @media (max-width: 1000px){ .board{grid-template-columns: repeat(2,minmax(180px,1fr));} }
    @media (max-width: 700px){ .board{grid-template-columns: 1fr;} }
  `]
})
export class ControlCenterTaskBoardComponent implements OnInit {
  readonly state = inject(ControlCenterStateFacade);
  cols: CcTaskStatus[] = ['backlog', 'proposed', 'running', 'blocked', 'review', 'verified', 'done', 'failed'];
  tasks: CcTaskCard[] = [];

  ngOnInit(): void {
    this.state.tasks$.subscribe((rows) => {
      this.tasks = rows.map((row) => ({
        id: row.id,
        title: row.title,
        description: row.description || '',
        status: this.toStatus(row.status),
        riskLevel: this.toRisk(row.priority),
        assignedWorkerId: null,
        preferredModel: null,
        artifactIds: [],
        verificationSummary: null,
      }));
    });
    this.state.loadTasks();
  }

  byStatus(s: CcTaskStatus): CcTaskCard[] { return this.tasks.filter(t => t.status === s); }
  private toStatus(status: string): CcTaskStatus {
    const v = String(status || '').toLowerCase();
    if (v === 'todo') return 'backlog';
    if (v === 'in_progress') return 'running';
    if (v === 'completed') return 'done';
    if (['backlog', 'proposed', 'running', 'blocked', 'review', 'verified', 'done', 'failed'].includes(v)) return v as CcTaskStatus;
    return 'backlog';
  }
  private toRisk(priority: string): 'low' | 'medium' | 'high' | 'critical' {
    const p = String(priority || '').toLowerCase();
    if (p === 'critical') return 'critical';
    if (p === 'high') return 'high';
    if (p === 'low') return 'low';
    return 'medium';
  }
  riskTone(r: string): 'neutral'|'ok'|'warn'|'danger'|'info' { return r === 'low' ? 'ok' : r === 'medium' ? 'info' : r === 'high' ? 'warn' : 'danger'; }
  verificationTone(s: string): 'neutral'|'ok'|'warn'|'danger'|'info' { return s === 'passed' ? 'ok' : s === 'failed' ? 'danger' : s === 'running' ? 'info' : 'neutral'; }
}
