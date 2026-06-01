import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgFor, NgIf } from '@angular/common';
import { StatusChipComponent } from './status-chip.component';

interface DecisionRow {
  id: string;
  actionId: string;
  decision: 'allow' | 'deny' | 'require_approval';
  reason: string;
  matchedRuleIds: string[];
}

@Component({
  standalone: true,
  selector: 'app-control-center-policy-approval',
  imports: [NgFor, NgIf, FormsModule, StatusChipComponent],
  template: `
    <h2>Policies & Approvals</h2>
    <div class="grid">
      <section class="panel">
        <h4>Decision Log</h4>
        <div class="row" *ngFor="let d of decisions">
          <div>
            <strong>{{ d.actionId }}</strong>
            <p class="muted">{{ d.reason }} · Rules: {{ d.matchedRuleIds.join(', ') || 'n/a' }}</p>
          </div>
          <app-status-chip [label]="d.decision" [tone]="tone(d.decision)" />
        </div>
      </section>

      <section class="panel">
        <h4>Approval Gate</h4>
        <label>Action ID <input [(ngModel)]="pendingActionId" placeholder="z.B. tc-103" /></label>
        <label>Tool Call ID <input [(ngModel)]="pendingToolCallId" placeholder="z.B. tool-77" /></label>
        <button (click)="approve()" [disabled]="!pendingActionId || !pendingToolCallId">Narrow Approval senden</button>
        <p class="muted">Es wird nur die konkrete Aktion freigegeben, keine Wildcard.</p>
        <pre *ngIf="lastPayload" class="raw">{{ lastPayload }}</pre>
      </section>
    </div>
  `,
  styles: [`.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.panel{border:1px solid #1f2937;border-radius:10px;padding:10px;background:#0f172a}.row{display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid #1f2937;padding:6px 0}.muted{color:#94a3b8;font-size:12px}label{display:flex;flex-direction:column;gap:4px;margin:6px 0}input{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px}.raw{background:#111827;border:1px solid #1f2937;border-radius:8px;padding:8px;white-space:pre-wrap}@media (max-width:900px){.grid{grid-template-columns:1fr}}`]
})
export class ControlCenterPolicyApprovalComponent {
  decisions: DecisionRow[] = [
    { id: 'd1', actionId: 'tc-101', decision: 'allow', reason: 'read-only command', matchedRuleIds: ['R-READ-01'] },
    { id: 'd2', actionId: 'tc-102', decision: 'deny', reason: 'path /secrets denied', matchedRuleIds: ['R-PATH-SECRET'] },
    { id: 'd3', actionId: 'tc-103', decision: 'require_approval', reason: 'write in protected path', matchedRuleIds: ['R-WRITE-PROTECTED'] },
  ];

  pendingActionId = '';
  pendingToolCallId = '';
  lastPayload = '';

  tone(d: DecisionRow['decision']): 'neutral'|'ok'|'warn'|'danger'|'info' {
    if (d === 'allow') return 'ok';
    if (d === 'deny') return 'danger';
    return 'warn';
  }

  approve(): void {
    const payload = {
      action_id: this.pendingActionId,
      tool_call_id: this.pendingToolCallId,
      scope: 'single_action',
      approved_by: 'ui-operator',
    };
    this.lastPayload = JSON.stringify(payload, null, 2);
  }
}
