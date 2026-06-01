import { Component } from '@angular/core';
import { NgFor, NgIf } from '@angular/common';
import { CcTaskCard, CcTaskStatus } from '../models/control-center.models';
import { StatusChipComponent } from './status-chip.component';

@Component({
  standalone: true,
  selector: 'app-control-center-task-board',
  imports: [NgFor, NgIf, StatusChipComponent],
  template: `
    <h2>Task Board</h2>
    <div class="board">
      <section class="col" *ngFor="let col of cols">
        <h3>{{ col }}</h3>
        <article class="card" *ngFor="let t of byStatus(col)">
          <strong>{{ t.title }}</strong>
          <div class="meta">
            <app-status-chip [label]="t.riskLevel" [tone]="riskTone(t.riskLevel)" />
            <app-status-chip [label]="t.verificationSummary?.status || 'not_run'" [tone]="verificationTone(t.verificationSummary?.status || 'not_run')" />
          </div>
          <div class="muted">Worker: {{ t.assignedWorkerId || 'n/a' }} · Modell: {{ t.preferredModel || 'n/a' }}</div>
        </article>
        <p *ngIf="!byStatus(col).length" class="muted">Keine Eintraege</p>
      </section>
    </div>
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
export class ControlCenterTaskBoardComponent {
  cols: CcTaskStatus[] = ['backlog', 'proposed', 'running', 'blocked', 'review', 'verified', 'done', 'failed'];
  tasks: CcTaskCard[] = [
    { id:'t1', title:'Policy Inspector verdrahten', description:'', status:'running', riskLevel:'high', assignedWorkerId:'alpha', preferredModel:'qwen2.5-coder:7b', artifactIds:['a1'], verificationSummary:{status:'running',testCount:12,passedCount:6,failedCount:0}},
    { id:'t2', title:'Artifact Viewer hardening', description:'', status:'review', riskLevel:'medium', assignedWorkerId:'beta', preferredModel:'gpt-4.1-mini', artifactIds:['a2'], verificationSummary:{status:'passed',testCount:18,passedCount:18,failedCount:0}},
    { id:'t3', title:'Approval Gate MVP', description:'', status:'blocked', riskLevel:'critical', assignedWorkerId:null, preferredModel:null, artifactIds:[], verificationSummary:null},
  ];

  byStatus(s: CcTaskStatus): CcTaskCard[] { return this.tasks.filter(t => t.status === s); }
  riskTone(r: string): 'neutral'|'ok'|'warn'|'danger'|'info' { return r === 'low' ? 'ok' : r === 'medium' ? 'info' : r === 'high' ? 'warn' : 'danger'; }
  verificationTone(s: string): 'neutral'|'ok'|'warn'|'danger'|'info' { return s === 'passed' ? 'ok' : s === 'failed' ? 'danger' : s === 'running' ? 'info' : 'neutral'; }
}
