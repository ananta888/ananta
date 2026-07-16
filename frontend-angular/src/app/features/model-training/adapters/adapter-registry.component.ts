import { CommonModule } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { TableShellComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { StatusBadgeComponent } from '../../../shared/ui/state';
import { ModelTrainingFacade } from '../model-training.facade';
import { AdapterExportResult, AdapterSummary } from '../model-training.models';
import { apiErrorMessage, idempotencyKey, shortHash, trainingStatusTone } from '../model-training-status';
import { BrowserArtifactDownloadService } from './browser-artifact-download.service';

type AdapterAction = 'approve' | 'reject' | 'deprecate' | 'rollback';

@Component({
  selector: 'app-model-training-adapter-registry',
  standalone: true,
  imports: [CommonModule, FormsModule, FormFieldComponent, SectionCardComponent, StatusBadgeComponent, TableShellComponent],
  template: `
    <app-table-shell
      title="Adapter-Registry"
      subtitle="Importierte und trainierte Adapter mit explizitem Lifecycle."
      [loading]="facade.loadingAdapters()"
      [empty]="!facade.loadingAdapters() && facade.adapters().length === 0"
      emptyTitle="Noch keine Adapter"
      refreshLabel="Aktualisieren"
      (refresh)="facade.loadAdapters()">
      <table class="standard-table table-min-600" data-testid="training-adapter-table">
        <thead><tr><th>Name / Version</th><th>Status</th><th>Basismodell</th><th>Methode</th><th>Score</th><th>SHA-256</th><th>Aktiv</th></tr></thead>
        <tbody>@for (adapter of facade.adapters(); track adapter.id + ':' + adapter.version) {
          <tr class="adapter-row" [class.selected]="facade.selectedAdapter()?.id === adapter.id">
            <td>
              <button
                type="button"
                class="table-entity-button"
                [attr.aria-current]="facade.selectedAdapter()?.id === adapter.id ? 'true' : null"
                [attr.aria-label]="'Adapter ' + adapter.name + ' Version ' + adapter.version + ' öffnen'"
                (click)="facade.selectAdapter(adapter)">
                <strong>{{ adapter.name }}</strong><span class="muted font-sm">v{{ adapter.version }} · {{ adapter.id }}</span>
              </button>
            </td>
            <td><app-status-badge [label]="adapter.status" [tone]="tone(adapter.status)" [dot]="true" /></td>
            <td>{{ adapter.base_model_id }}</td><td>{{ adapter.method || '-' }}</td><td>{{ adapter.score ?? '-' }}</td>
            <td class="font-mono font-sm">{{ hash(adapter.sha256) }}</td><td>{{ adapter.active ? 'ja' : 'nein' }}</td>
          </tr>
        }</tbody>
      </table>
    </app-table-shell>

    @if (facade.selectedAdapter(); as adapter) {
      <app-section-card [title]="adapter.name + ' v' + adapter.version" [subtitle]="adapter.base_model_id" variant="warning">
        <div section-actions><app-status-badge [label]="adapter.status" [tone]="tone(adapter.status)" [dot]="true" /></div>
        <div class="action-grid">
          <app-form-field label="Lifecycle-Aktion">
            <select [(ngModel)]="action">
              <option value="approve" [disabled]="!canAction(adapter, 'approve')">Freigeben</option>
              <option value="reject" [disabled]="!canAction(adapter, 'reject')">Ablehnen</option>
              <option value="deprecate" [disabled]="!canAction(adapter, 'deprecate')">Deprecaten</option>
              <option value="rollback" [disabled]="!canAction(adapter, 'rollback')">Registry-Statusrollback</option>
            </select>
          </app-form-field>
          <app-form-field label="Begründung" [required]="true"><input [(ngModel)]="reason" maxlength="500" /></app-form-field>
        </div>
        <label class="confirmation"><input type="checkbox" [(ngModel)]="confirmed" /> Ich bestätige die explizite Registry-Änderung.</label>
        <div class="row gap-sm mt-sm wrap">
          <button type="button" (click)="decide()" [disabled]="busy() || !confirmed || reason.trim().length < 8 || !canAction(adapter, action)">Aktion ausführen</button>
          <button type="button" class="secondary" (click)="exportAdapter()" [disabled]="busy() || !canExport(adapter)">Hashverifiziert exportieren</button>
        </div>
        @if (action === 'approve' && !canAction(adapter, 'approve')) { <p class="muted" role="status">Approval benötigt eine bestandene Evaluation und den Status evaluated.</p> }
        @if (error()) { <div class="state-banner error" role="alert">{{ error() }}</div> }
        @if (exportResult(); as result) {
          <div class="state-banner success" role="status">
            Export-Artifact {{ result.artifact_id }} · SHA-256 {{ result.sha256 }}
            <button type="button" class="secondary" (click)="downloadExport()" [disabled]="busy()">
              ZIP authentifiziert herunterladen
            </button>
          </div>
        }
      </app-section-card>
    }
  `,
  styles: [`
    .adapter-row.selected { background:color-mix(in srgb,var(--accent) 10%,transparent); }
    .table-entity-button { display:grid; gap:2px; width:100%; padding:0; border:0; background:transparent; color:inherit; text-align:left; }
    .action-grid { display:grid; grid-template-columns:1fr 2fr; gap:12px; }
    .confirmation { display:flex; gap:8px; align-items:flex-start; margin-top:12px; }
    @media (max-width:700px) { .action-grid { grid-template-columns:1fr; } }
  `],
})
export class AdapterRegistryComponent {
  readonly facade = inject(ModelTrainingFacade);
  private readonly downloads = inject(BrowserArtifactDownloadService);
  readonly tone = trainingStatusTone;
  readonly hash = shortHash;
  action: AdapterAction = 'approve';
  reason = '';
  confirmed = false;
  readonly busy = signal(false);
  readonly error = signal('');
  readonly exportResult = signal<AdapterExportResult | null>(null);

  canAction(adapter: AdapterSummary, action: AdapterAction): boolean {
    const status = String(adapter.status);
    if (action === 'approve') return status === 'evaluated' && this.facade.selectedEvaluation()?.passed === true;
    if (action === 'reject') return status === 'evaluated';
    if (action === 'deprecate') return ['approved', 'rejected'].includes(status);
    if (action === 'rollback') return ['approved', 'deprecated'].includes(status);
    return false;
  }

  canExport(adapter: AdapterSummary): boolean {
    return ['evaluated', 'approved', 'rejected', 'deprecated'].includes(String(adapter.status))
      && adapter.hash_verified === true
      && adapter.artifact_exists === true;
  }

  decide(): void {
    const adapter = this.facade.selectedAdapter();
    if (!adapter || !this.canAction(adapter, this.action) || !this.confirmed || this.reason.trim().length < 8 || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.facade.decideAdapter(adapter.id, this.action, {
      reason: this.reason,
      expected_version: adapter.registry_version ?? adapter.version,
      confirmed: true,
    }, idempotencyKey(`adapter-${this.action}`)).pipe(finalize(() => this.busy.set(false))).subscribe({
      next: () => { this.reason = ''; this.confirmed = false; },
      error: error => this.error.set(apiErrorMessage(error, 'Adapterstatus konnte nicht geändert werden.')),
    });
  }

  exportAdapter(): void {
    const adapter = this.facade.selectedAdapter();
    if (!adapter || !this.canExport(adapter) || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.facade.exportAdapter(adapter.id, idempotencyKey('adapter-export')).pipe(finalize(() => this.busy.set(false))).subscribe({
      next: result => this.exportResult.set(result),
      error: error => this.error.set(apiErrorMessage(error, 'Adapterexport ist fehlgeschlagen.')),
    });
  }

  downloadExport(): void {
    const exportResult = this.exportResult();
    if (!exportResult || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.facade.downloadAdapterExport(exportResult.artifact_id).pipe(
      finalize(() => this.busy.set(false)),
    ).subscribe({
      next: download => {
        if (
          download.sha256
          && download.sha256.toLowerCase() !== exportResult.sha256.toLowerCase()
        ) {
          this.error.set('Der heruntergeladene Export stimmt nicht mit dem angekündigten SHA-256 überein.');
          return;
        }
        this.downloads.save(download);
      },
      error: error => this.error.set(apiErrorMessage(error, 'Adapterexport konnte nicht heruntergeladen werden.')),
    });
  }
}
