import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AsyncPipe, DatePipe, NgFor, NgIf } from '@angular/common';
import { StatusChipComponent } from './status-chip.component';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { ApprovalsApiService } from '../../../services/approvals-api.service';
import { SystemFacade } from '../../system/system.facade';

interface DecisionRow {
  id: string;
  actionId: string;
  decision: 'allow' | 'deny' | 'require_approval';
  reason: string;
  matchedRuleIds: string[];
  toolCallId?: string;
}

// ALWA-010: pending ApprovalRequest row from /api/approvals — digest only
// as prefix, never raw tool arguments.
interface ApprovalRequestRow {
  request_id: string;
  task_id?: string;
  goal_id?: string;
  tool_name: string;
  digest_prefix: string;
  risk_class: string;
  k_class?: string;
  governance_mode: string;
  status: string;
  scope_summary: Record<string, unknown>;
  created_at: number;
  expires_at?: number;
  decision_reason?: string;
}

@Component({
  standalone: true,
  selector: 'app-control-center-policy-approval',
  imports: [NgFor, NgIf, FormsModule, StatusChipComponent, AsyncPipe, DatePipe],
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

      <section class="panel" style="grid-column: 1 / -1;">
        <h4>Pending ApprovalRequests (digest-gebunden)</h4>
        <div class="row" style="justify-content:flex-start; gap:8px; border-bottom:none;">
          <button (click)="loadApprovalRequests()">Aktualisieren</button>
        </div>
        <div class="row" *ngFor="let r of approvalRequests">
          <div>
            <strong>{{ r.tool_name }}</strong>
            <span class="muted"> · Digest {{ r.digest_prefix }}…</span>
            <p class="muted">
              Task: {{ r.task_id || '—' }} · Goal: {{ r.goal_id || '—' }} · Risiko: {{ r.risk_class }}
              · Governance: {{ r.governance_mode }}
              · läuft ab: {{ r.expires_at ? (r.expires_at * 1000 | date:'short') : '—' }}
            </p>
            <p class="muted" *ngIf="r.scope_summary && r.scope_summary['reason_code']">Grund: {{ r.scope_summary['reason_code'] }}</p>
          </div>
          <div style="display:flex; gap:6px; align-items:center;">
            <app-status-chip [label]="r.status" [tone]="r.status === 'pending' ? 'warn' : 'neutral'" />
            <button (click)="decideRequest(r, 'granted')" [disabled]="r.status !== 'pending'">Grant</button>
            <button (click)="decideRequest(r, 'denied')" [disabled]="r.status !== 'pending'">Deny</button>
          </div>
        </div>
        <p class="muted" *ngIf="!approvalRequests.length">Keine pending ApprovalRequests.</p>
        <p class="muted" *ngIf="approvalResultMessage">{{ approvalResultMessage }}</p>
        <p class="muted">Ein Grant gilt nur für exakt diesen Call (arguments_digest); Argumente werden nie roh angezeigt.</p>
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
  private approvalsApi = inject(ApprovalsApiService);
  private system = inject(SystemFacade);
  decisions: DecisionRow[] = [];
  selectedSessionId = '';
  pendingRows: DecisionRow[] = [];
  selectedPendingId = '';
  lastPayload = '';
  resultMessage = '';
  approvalRequests: ApprovalRequestRow[] = [];
  approvalResultMessage = '';

  loadApprovalRequests(): void {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) return;
    this.approvalsApi.listRequests(hub.url, 'pending').subscribe({
      next: (data) => (this.approvalRequests = data?.requests || []),
      error: () => (this.approvalResultMessage = 'ApprovalRequests konnten nicht geladen werden'),
    });
  }

  decideRequest(row: ApprovalRequestRow, decision: 'granted' | 'denied'): void {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) return;
    this.approvalsApi.decide(hub.url, row.request_id, decision).subscribe({
      next: () => {
        this.approvalResultMessage = `Request ${row.request_id.slice(0, 8)}… ${decision}`;
        this.loadApprovalRequests();
      },
      error: (err) => {
        const code = err?.error?.error || err?.status || 'unbekannt';
        this.approvalResultMessage = `Entscheidung fehlgeschlagen (${code}) — Request evtl. bereits entschieden oder abgelaufen`;
        this.loadApprovalRequests();
      },
    });
  }

  ngOnInit(): void {
    this.loadApprovalRequests();
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
