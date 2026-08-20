import { Component, Input, OnChanges, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { OperationPolicyInventoryDto, OperationPolicyInventoryItemDto } from '../models/operation-policy.model';
import { OperationPolicyApiService } from '../services/operation-policy-api.service';

@Component({
  selector: 'app-operation-policy-inventory',
  standalone: true,
  imports: [FormsModule],
  template: `
    <section class="operation-policy-card" data-testid="operation-policy-inventory">
      <header>
        <div>
          <p class="eyebrow">Hub control plane</p>
          <h3>Operation Allowlist</h3>
          <p class="muted">Read-only Sicht auf registrierte MCP- und API-Operationen.</p>
        </div>
        <button type="button" class="button-outline" (click)="load()" [disabled]="loading">Aktualisieren</button>
      </header>
      @if (error) {
        <p class="error-text">{{ error }}</p>
      } @else if (inventory) {
        <div class="policy-strip">
          <span>Revision {{ inventory.policy['revision'] ?? 0 }}</span>
          <span>{{ filteredItems.length }} / {{ inventory.registered_count }} sichtbar</span>
          <span class="hash">{{ inventory.policy['policy_hash'] || 'legacy' }}</span>
        </div>
        <div class="filters">
          <label>Transport
            <select [(ngModel)]="transportFilter">
              <option value="">Alle</option>
              @for (value of transportOptions; track value) { <option [value]="value">{{ value }}</option> }
            </select>
          </label>
          <label>Zugriff
            <select [(ngModel)]="accessFilter">
              <option value="">Alle</option>
              @for (value of accessOptions; track value) { <option [value]="value">{{ value }}</option> }
            </select>
          </label>
          <label>Status
            <select [(ngModel)]="statusFilter">
              <option value="">Alle</option>
              @for (value of statusOptions; track value) { <option [value]="value">{{ value }}</option> }
            </select>
          </label>
        </div>
        <div class="inventory-table" role="table" aria-label="Operation policy inventory">
          @for (item of filteredItems; track item.operation_id) {
            <article class="operation-row" role="row">
              <div>
                <strong>{{ item.operation_id }}</strong>
                <small>{{ item.http_method || item.transport }} {{ item.name_or_route }}</small>
              </div>
              <span class="pill">{{ item.access_class }} · {{ item.risk_class }}</span>
              <span class="pill" [class.denied]="!item.decision.allowed">{{ item.policy_status }}</span>
              <span class="reason">{{ item.decision.reason_code }}</span>
            </article>
          } @empty {
            <p class="muted empty">Keine Operation entspricht den Filtern.</p>
          }
        </div>
      } @else {
        <p class="muted">{{ loading ? 'Policy wird geladen ...' : 'Keine Policy-Daten verfügbar.' }}</p>
      }
    </section>
  `,
  styles: [`
    .operation-policy-card { margin-top: 1.5rem; padding: 1.25rem; border: 1px solid color-mix(in srgb, var(--accent, #b95f24) 45%, transparent); border-radius: 18px; background: linear-gradient(135deg, color-mix(in srgb, var(--card-bg, #fff) 94%, #f0b35f), var(--card-bg, #fff)); }
    header, .policy-strip, .filters, .operation-row { display: flex; align-items: center; gap: .8rem; }
    header { justify-content: space-between; }
    h3 { margin: 0; }
    .eyebrow { margin: 0 0 .25rem; text-transform: uppercase; letter-spacing: .14em; font-size: .68rem; font-weight: 800; color: var(--accent, #9a431a); }
    .policy-strip { margin: 1rem 0; padding: .7rem .85rem; border-radius: 10px; background: color-mix(in srgb, var(--accent, #b95f24) 10%, transparent); font-size: .78rem; }
    .hash { margin-left: auto; max-width: 15rem; overflow: hidden; text-overflow: ellipsis; font-family: monospace; white-space: nowrap; }
    .filters { flex-wrap: wrap; margin-bottom: .85rem; }
    .filters label { display: grid; gap: .25rem; min-width: 9rem; font-size: .75rem; font-weight: 700; }
    .operation-row { display: grid; grid-template-columns: minmax(14rem, 1fr) auto auto minmax(9rem, auto); padding: .72rem 0; border-top: 1px solid color-mix(in srgb, currentColor 12%, transparent); }
    .operation-row div { min-width: 0; }
    .operation-row strong, .operation-row small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .operation-row strong { font-family: monospace; font-size: .8rem; }
    .operation-row small, .reason { color: var(--muted, #71685e); font-size: .72rem; }
    .pill { padding: .25rem .48rem; border-radius: 999px; background: #dcebd8; color: #23451f; font-size: .7rem; font-weight: 800; }
    .pill.denied { background: #f4d7cf; color: #742717; }
    .empty { padding: 1rem 0; }
    @media (max-width: 760px) { .operation-row { grid-template-columns: 1fr auto; } .reason { grid-column: 1 / -1; } .hash { display: none; } }
  `],
})
export class OperationPolicyInventoryComponent implements OnChanges {
  @Input() baseUrl = '';
  private readonly api = inject(OperationPolicyApiService);
  inventory: OperationPolicyInventoryDto | null = null;
  loading = false;
  error = '';
  transportFilter = '';
  accessFilter = '';
  statusFilter = '';

  ngOnChanges(): void {
    if (this.baseUrl) this.load();
  }

  load(): void {
    if (!this.baseUrl) return;
    this.loading = true;
    this.error = '';
    this.api.inventory(this.baseUrl).subscribe({
      next: inventory => { this.inventory = inventory; this.loading = false; },
      error: () => { this.error = 'Operation-Policy ist nicht verfügbar oder erfordert Admin-Rechte.'; this.loading = false; },
    });
  }

  private options(selector: (item: OperationPolicyInventoryItemDto) => string): string[] {
    return [...new Set((this.inventory?.items || []).map(selector).filter(Boolean))].sort();
  }

  get transportOptions(): string[] { return this.options(item => item.transport); }
  get accessOptions(): string[] { return this.options(item => item.access_class); }
  get statusOptions(): string[] { return this.options(item => item.policy_status); }

  get filteredItems(): OperationPolicyInventoryItemDto[] {
    return (this.inventory?.items || []).filter(item =>
      (!this.transportFilter || item.transport === this.transportFilter)
      && (!this.accessFilter || item.access_class === this.accessFilter)
      && (!this.statusFilter || item.policy_status === this.statusFilter),
    );
  }
}
