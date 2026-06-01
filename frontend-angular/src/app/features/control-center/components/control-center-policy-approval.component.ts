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
  toolCallId?: string;
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
        <div *ngIf="pendingRows.length; else noPending">
          <label>Pending Action
            <select [(ngModel)]="selectedPendingId">
              <option *ngFor="let p of pendingRows" [value]="p.id">
                {{ p.actionId }} · {{ p.reason }} · {{ p.toolCallId || p.id }}
              </option>
            </select>
          </label>
          <button (click)="approveSelected()" [disabled]="!selectedPendingId">Narrow Approval senden</button>
        </div>
        <ng-template #noPending>
          <p class="muted">Keine pending approvals für diese Session.</p>
        </ng-template>
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
  pendingRows: DecisionRow[] = [];
  selectedPendingId = '';
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
        toolCallId: row.tool_call_id,
      }));
      this.pendingRows = this.decisions.filter((d) => d.decision === 'require_approval' && !!d.toolCallId);
      if (!this.selectedPendingId || !this.pendingRows.some((d) => d.id === this.selectedPendingId)) {
        this.selectedPendingId = this.pendingRows[0]?.id || '';
      }
    });
    this.state.loadSessions();
  }

  tone(d: DecisionRow['decision']): 'neutral'|'ok'|'warn'|'danger'|'info' {
    if (d === 'allow') return 'ok';
    if (d === 'deny') return 'danger';
    return 'warn';
  }

  approveSelected(): void {
    const pending = this.pendingRows.find((row) => row.id === this.selectedPendingId);
    if (!pending || !pending.toolCallId) return;
    const payload = {
      action_id: pending.actionId,
      tool_call_id: pending.toolCallId,
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
