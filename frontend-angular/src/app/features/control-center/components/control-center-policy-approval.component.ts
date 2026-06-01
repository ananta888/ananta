import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AsyncPipe, NgFor, NgIf } from '@angular/common';
import { StatusChipComponent } from './status-chip.component';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';

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
  imports: [NgFor, NgIf, FormsModule, StatusChipComponent, AsyncPipe],
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
        <label>Session
          <select [(ngModel)]="selectedSessionId" (ngModelChange)="state.loadPolicyDecisions(selectedSessionId)">
            <option *ngFor="let s of (state.sessions$ | async) || []" [value]="s.id">{{ s.id }}</option>
          </select>
        </label>
        <label>Action ID <input [(ngModel)]="pendingActionId" placeholder="z.B. tc-103" /></label>
        <label>Tool Call ID <input [(ngModel)]="pendingToolCallId" placeholder="z.B. tool-77" /></label>
        <button (click)="approve()" [disabled]="!pendingActionId || !pendingToolCallId">Narrow Approval senden</button>
        <p class="muted">Es wird nur die konkrete Aktion freigegeben, keine Wildcard.</p>
        <p class="muted" *ngIf="resultMessage">{{ resultMessage }}</p>
        <pre *ngIf="lastPayload" class="raw">{{ lastPayload }}</pre>
      </section>
    </div>
  `,
  styles: [`.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.panel{border:1px solid #1f2937;border-radius:10px;padding:10px;background:#0f172a}.row{display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid #1f2937;padding:6px 0}.muted{color:#94a3b8;font-size:12px}label{display:flex;flex-direction:column;gap:4px;margin:6px 0}input,select{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px}.raw{background:#111827;border:1px solid #1f2937;border-radius:8px;padding:8px;white-space:pre-wrap}@media (max-width:900px){.grid{grid-template-columns:1fr}}`]
})
export class ControlCenterPolicyApprovalComponent implements OnInit {
  readonly state = inject(ControlCenterStateFacade);
  decisions: DecisionRow[] = [];
  selectedSessionId = '';

  pendingActionId = '';
  pendingToolCallId = '';
  lastPayload = '';
  resultMessage = '';

  ngOnInit(): void {
    this.state.sessions$.subscribe((sessions) => {
      if (!this.selectedSessionId && sessions.length) {
        this.selectedSessionId = sessions[0].id;
      }
      if (this.selectedSessionId) {
        this.state.loadPolicyDecisions(this.selectedSessionId);
      }
    });
    this.state.policyDecisions$.subscribe((rows) => {
      this.decisions = rows.map((row) => ({
        id: row.id,
        actionId: row.action_id || row.id,
        decision: (row.decision as 'allow' | 'deny' | 'require_approval'),
        reason: row.reason,
        matchedRuleIds: row.matched_rule_ids || [],
      }));
    });
    this.state.loadSessions();
  }

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
      session_id: this.selectedSessionId,
    };
    this.lastPayload = JSON.stringify(payload, null, 2);
    const req = this.state.approveAction(payload);
    if (!req) {
      this.resultMessage = 'Approval fehlgeschlagen: kein Hub konfiguriert';
      return;
    }
    req.subscribe({
      next: () => {
        this.resultMessage = 'Approval erfolgreich gesendet';
        if (this.selectedSessionId) this.state.loadPolicyDecisions(this.selectedSessionId);
      },
      error: () => {
        this.resultMessage = 'Approval abgelehnt oder fehlgeschlagen';
      },
    });
  }
}
