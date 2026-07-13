import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges, inject } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApprovalsApiService } from '../../services/approvals-api.service';
import { OpsApprovalRequest } from './ops.models';

@Component({
  selector: 'app-ops-approval-panel',
  standalone: true,
  imports: [DatePipe, FormsModule],
  template: `
    <section class="approval-panel">
      <div class="row flex-between">
        <div>
          <strong>Freigaben</strong>
          <span class="muted"> · argumentgebunden, einmalig und auditierbar</span>
        </div>
        <button type="button" class="secondary btn-small" (click)="load()" [disabled]="loading">Aktualisieren</button>
      </div>
      @if (message) { <div class="state-banner" [class.error]="messageError">{{ message }}</div> }
      @if (loading && !requests.length) { <p class="muted">Freigaben werden geladen…</p> }
      @if (!loading && !requests.length) { <p class="muted">Keine ausstehenden Ops-Freigaben.</p> }
      @for (request of requests; track request.request_id) {
        <article class="approval-row" [class.focused]="request.request_id === focusRequestId">
          <div class="approval-main">
            <div class="row">
              <strong>{{ request.tool_name }}</strong>
              <span class="status-pill">{{ request.risk_class || 'unknown' }}</span>
              <code>{{ request.digest_prefix }}</code>
            </div>
            <div class="muted font-sm">
              Request {{ request.request_id }}
              @if (request.expires_at) { · gültig bis {{ request.expires_at * 1000 | date:'dd.MM.yyyy HH:mm:ss' }} }
            </div>
            @if (request.scope_summary && objectEntries(request.scope_summary).length) {
              <div class="scope-list">
                @for (item of objectEntries(request.scope_summary); track item[0]) {
                  <span><strong>{{ item[0] }}:</strong> {{ item[1] }}</span>
                }
              </div>
            }
          </div>
          <div class="approval-actions">
            <input [(ngModel)]="reasons[request.request_id]" placeholder="Begründung (empfohlen)" aria-label="Begründung" />
            <button type="button" class="primary btn-small" (click)="decide(request, 'granted')" [disabled]="decidingId === request.request_id">Freigeben</button>
            <button type="button" class="danger btn-small" (click)="decide(request, 'denied')" [disabled]="decidingId === request.request_id">Ablehnen</button>
          </div>
        </article>
      }
      <p class="muted font-sm no-margin">Eine Freigabe führt die Aktion nicht automatisch aus. Starten Sie die exakt gebundene Aktion danach bewusst erneut.</p>
    </section>
  `,
  styles: [`
    .approval-panel { display: grid; gap: 10px; }
    .approval-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-raised); }
    .approval-row.focused { border-color: var(--tone-warning); box-shadow: 0 0 0 2px color-mix(in srgb, var(--tone-warning) 25%, transparent); }
    .approval-main { min-width: 0; }
    .approval-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    .approval-actions input { min-width: 220px; }
    .status-pill { padding: 2px 7px; border-radius: 999px; background: var(--warning-bg); color: var(--warning); font-size: 12px; }
    .scope-list { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 6px; font-size: 12px; }
    code { overflow-wrap: anywhere; }
    @media (max-width: 900px) { .approval-row { grid-template-columns: 1fr; } .approval-actions { justify-content: flex-start; } }
  `],
})
export class OpsApprovalPanelComponent implements OnChanges {
  private api = inject(ApprovalsApiService);
  @Input({ required: true }) baseUrl = '';
  @Input() focusRequestId = '';
  @Input() refreshGeneration = 0;
  @Output() decided = new EventEmitter<{ request: OpsApprovalRequest; decision: 'granted' | 'denied' }>();

  requests: OpsApprovalRequest[] = [];
  reasons: Record<string, string> = {};
  loading = false;
  decidingId = '';
  message = '';
  messageError = false;

  ngOnChanges(changes: SimpleChanges): void {
    if (this.baseUrl && (changes['baseUrl'] || changes['refreshGeneration'] || changes['focusRequestId'])) this.load();
  }

  load(preserveMessage = false): void {
    if (!this.baseUrl) return;
    this.loading = true;
    if (!preserveMessage) this.message = '';
    this.api.listRequests(this.baseUrl, 'pending').subscribe({
      next: (data) => {
        const all = Array.isArray(data?.requests) ? data.requests as OpsApprovalRequest[] : [];
        this.requests = all.filter((request) => request.tool_name.startsWith('git.') || request.tool_name.startsWith('docker.') || request.tool_name.startsWith('compose.'));
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.messageError = true;
        this.message = 'Freigaben konnten nicht geladen werden.';
      },
    });
  }

  decide(request: OpsApprovalRequest, decision: 'granted' | 'denied'): void {
    this.decidingId = request.request_id;
    this.api.decide(this.baseUrl, request.request_id, decision, this.reasons[request.request_id]).subscribe({
      next: () => {
        this.decidingId = '';
        this.messageError = false;
        this.message = decision === 'granted'
          ? 'Freigabe erteilt. Die gebundene Aktion kann jetzt erneut ausgelöst werden.'
          : 'Aktion wurde abgelehnt.';
        this.decided.emit({ request, decision });
        this.load(true);
      },
      error: (error) => {
        this.decidingId = '';
        this.messageError = true;
        this.message = `Freigabeentscheidung fehlgeschlagen: ${error?.error?.error || error?.message || 'unbekannter Fehler'}`;
      },
    });
  }

  objectEntries(value: Record<string, unknown>): Array<[string, unknown]> {
    return Object.entries(value);
  }
}
