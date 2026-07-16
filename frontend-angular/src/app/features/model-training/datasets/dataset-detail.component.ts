import { CommonModule } from '@angular/common';
import { A11yModule } from '@angular/cdk/a11y';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { MetricCardComponent, TableShellComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { EmptyStateComponent, StatusBadgeComponent } from '../../../shared/ui/state';
import { ModelTrainingFacade } from '../model-training.facade';
import { DatasetRecord } from '../model-training.models';
import { apiErrorMessage, boundedText, idempotencyKey, trainingStatusTone } from '../model-training-status';

@Component({
  selector: 'app-model-training-dataset-detail',
  standalone: true,
  imports: [A11yModule, CommonModule, FormsModule, EmptyStateComponent, FormFieldComponent, MetricCardComponent, SectionCardComponent, StatusBadgeComponent, TableShellComponent],
  template: `
    @if (facade.selectedDataset(); as dataset) {
      <app-section-card [title]="dataset.name" subtitle="Split, Validierung und begrenzte Record-Vorschau" variant="primary">
        <div section-actions>
          <app-status-badge [label]="dataset.validation_status || dataset.status" [tone]="tone(dataset.validation_status || dataset.status)" [dot]="true" />
          <button type="button" class="secondary btn-small" (click)="validate()" [disabled]="busy">Erneut validieren</button>
        </div>

        <div class="grid cols-4 dataset-metrics">
          <app-metric-card label="Records" [value]="dataset.record_count" />
          <app-metric-card label="Training" [value]="dataset.train_record_count" />
          <app-metric-card label="Validation" [value]="dataset.validation_record_count" />
          <app-metric-card label="Duplikate" [value]="dataset.duplicate_record_count || 0" [tone]="dataset.duplicate_record_count ? 'warning' : 'success'" />
        </div>

        <div class="split-controls">
          <app-form-field label="Validation-Ratio">
            <input type="number" min="0.05" max="0.5" step="0.05" [(ngModel)]="validationRatio" [disabled]="busy" />
          </app-form-field>
          <app-form-field label="Seed">
            <input type="number" min="0" step="1" [(ngModel)]="seed" [disabled]="busy" />
          </app-form-field>
          <button type="button" (click)="split()" [disabled]="busy || !splitValid()">Split anwenden</button>
        </div>
        @if (splitValid()) {
          <p class="muted font-sm" role="status">
            Vorschau: ca. {{ projectedCounts().train }} Training- und {{ projectedCounts().validation }} Validation-Records.
            Verbindlich sind die leak-geprüften Counts des Hub-Ergebnisses.
          </p>
        }

        @if (report(); as report) {
          <div class="validation-summary" [class.invalid]="!report.valid" role="status" aria-live="polite">
            <strong>{{ report.valid ? 'Validierung bestanden' : 'Validierung blockiert Training' }}</strong>
            <span>Akzeptiert {{ report.accepted_records }} · Abgelehnt {{ report.rejected_records }} · Secrets {{ report.secret_findings }} · PII {{ report.pii_findings || 0 }}</span>
          </div>
          @if (report.issues.length) {
            <ul class="issue-list" aria-label="Validierungsprobleme">
              @for (issue of report.issues; track issue.code + ':' + (issue.record_index ?? 'all')) {
                <li>
                  <app-status-badge [label]="issue.severity" [tone]="tone(issue.severity === 'error' ? 'failed' : 'validating')" />
                  <code>{{ issue.code }}</code> · {{ issue.count || 1 }} Treffer
                  @if (issue.record_index !== undefined) { · Record {{ issue.record_index }} }
                  @if (issue.field) { · Feld {{ issue.field }} }
                  @if (issue.message) { <span class="muted"> · {{ text(issue.message) }}</span> }
                </li>
              }
            </ul>
          }
        }
        @if (error) { <div class="state-banner error" role="alert">{{ error }}</div> }
      </app-section-card>

      @if (splitConfirmationOpen) {
        <div class="dialog-backdrop" (click)="cancelSplitConfirmation()">
          <section
            class="confirmation-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="split-confirmation-title"
            aria-describedby="split-confirmation-description"
            cdkTrapFocus
            [cdkTrapFocusAutoCapture]="true"
            (click)="$event.stopPropagation()"
            (keydown.escape)="cancelSplitConfirmation()">
            <h3 id="split-confirmation-title">Vorhandenen Split ersetzen?</h3>
            <p id="split-confirmation-description">
              Der bestehende Train-/Validation-Split wird durch einen deterministischen Hub-Lauf ersetzt.
            </p>
            <dl class="confirmation-summary">
              <div><dt>Validation-Ratio</dt><dd>{{ validationRatio }}</dd></div>
              <div><dt>Seed</dt><dd>{{ seed }}</dd></div>
              <div><dt>Erwartetes Training</dt><dd>{{ projectedCounts().train }}</dd></div>
              <div><dt>Erwartete Validation</dt><dd>{{ projectedCounts().validation }}</dd></div>
            </dl>
            <div class="dialog-actions">
              <button type="button" class="secondary" (click)="cancelSplitConfirmation()">Abbrechen</button>
              <button type="button" (click)="confirmSplitReplacement()">Vorhandenen Split ersetzen</button>
            </div>
          </section>
        </div>
      }

      <app-table-shell
        title="Record-Vorschau"
        subtitle="Maximal 25 Records pro Seite; Inhalte werden als Text interpoliert."
        [loading]="facade.loadingRecords()"
        [empty]="!facade.loadingRecords() && facade.records().length === 0"
        loadingLabel="Preview wird geladen"
        emptyTitle="Keine Records in diesem Split">
        <div table-toolbar class="preview-toolbar">
          <label>Split
            <select [ngModel]="facade.recordSplit()" (ngModelChange)="changeSplit($event)">
              <option value="train">Training</option>
              <option value="validation">Validation</option>
            </select>
          </label>
          <button type="button" class="secondary btn-small" (click)="facade.previousRecordPage()" [disabled]="!facade.hasPreviousRecordPage()">Zurück</button>
          <button type="button" class="secondary btn-small" (click)="facade.nextRecordPage()" [disabled]="!facade.recordsNextCursor()">Weiter</button>
        </div>
        <table class="standard-table table-min-600" data-testid="training-record-preview">
          <thead><tr><th>#</th><th>Split</th><th>Prompt / Nachrichten</th><th>Output</th><th>Prüfung</th></tr></thead>
          <tbody>
            @for (record of facade.records(); track record.id || record.index) {
              <tr>
                <td>{{ record.index }}</td>
                <td>{{ record.split }}</td>
                <td class="preview-text">{{ prompt(record) }}</td>
                <td class="preview-text">{{ text(record.output) }}</td>
                <td>{{ record.valid === false ? (record.reason_codes || []).join(', ') : 'ok' }}</td>
              </tr>
            }
          </tbody>
        </table>
      </app-table-shell>
    } @else {
      <app-empty-state title="Dataset auswählen" description="Wählen Sie im Katalog ein Dataset für Split, Validierung und Vorschau." [compact]="true" />
    }
  `,
  styles: [`
    .dataset-metrics { margin-bottom:14px; }
    .split-controls { display:grid; grid-template-columns:1fr 1fr auto; gap:10px; align-items:end; }
    .validation-summary { display:flex; gap:12px; flex-wrap:wrap; margin-top:12px; padding:10px; border:1px solid var(--tone-success); border-radius:8px; }
    .validation-summary.invalid { border-color:var(--tone-error); background:var(--danger-bg); }
    .issue-list { display:grid; gap:5px; padding-left:20px; }
    .preview-toolbar { display:flex; align-items:end; gap:6px; }
    .preview-toolbar label { display:grid; gap:3px; }
    .preview-text { white-space:pre-wrap; max-width:40ch; overflow-wrap:anywhere; }
    .dialog-backdrop { position:fixed; inset:0; z-index:1000; display:grid; place-items:center; padding:20px; background:rgb(0 0 0 / 55%); }
    .confirmation-dialog { width:min(520px,100%); display:grid; gap:14px; padding:20px; border:1px solid var(--border); border-radius:12px; background:var(--card-bg); color:var(--fg); box-shadow:0 18px 50px rgb(0 0 0 / 35%); }
    .confirmation-dialog h3,.confirmation-dialog p { margin:0; }
    .confirmation-summary { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin:0; }
    .confirmation-summary div { padding:10px; border:1px solid var(--border); border-radius:8px; }
    .confirmation-summary dt { color:var(--text-muted); font-size:.8rem; }
    .confirmation-summary dd { margin:3px 0 0; font-weight:700; }
    .dialog-actions { display:flex; justify-content:flex-end; gap:8px; flex-wrap:wrap; }
    @media (max-width:700px) { .dataset-metrics,.split-controls { grid-template-columns:1fr; } }
  `],
})
export class DatasetDetailComponent {
  readonly facade = inject(ModelTrainingFacade);
  readonly tone = trainingStatusTone;
  readonly text = (value: unknown) => boundedText(value, 800);
  validationRatio = 0.2;
  seed = 42;
  busy = false;
  error = '';
  splitConfirmationOpen = false;

  report() { return this.facade.selectedDataset()?.validation_report || null; }

  splitValid(): boolean {
    return Number(this.validationRatio) >= 0.05 && Number(this.validationRatio) <= 0.5 && Number.isInteger(Number(this.seed)) && Number(this.seed) >= 0;
  }

  projectedCounts(): { train: number; validation: number } {
    const total = Math.max(0, Number(this.facade.selectedDataset()?.record_count || 0));
    const validation = Math.round(total * Number(this.validationRatio));
    return { train: Math.max(0, total - validation), validation };
  }

  split(): void {
    const dataset = this.facade.selectedDataset();
    if (!dataset || !this.splitValid() || this.busy) return;
    const hasSplit = dataset.train_record_count > 0 || dataset.validation_record_count > 0;
    if (hasSplit) {
      this.splitConfirmationOpen = true;
      return;
    }
    this.submitSplit(dataset.id, false);
  }

  confirmSplitReplacement(): void {
    const dataset = this.facade.selectedDataset();
    this.splitConfirmationOpen = false;
    if (!dataset || !this.splitValid() || this.busy) return;
    this.submitSplit(dataset.id, true);
  }

  cancelSplitConfirmation(): void {
    this.splitConfirmationOpen = false;
  }

  private submitSplit(datasetId: string, replaceExisting: boolean): void {
    this.busy = true;
    this.error = '';
    this.facade.splitDataset(datasetId, Number(this.validationRatio), Number(this.seed), replaceExisting, idempotencyKey('dataset-split'))
      .pipe(finalize(() => this.busy = false)).subscribe({
        error: error => this.error = apiErrorMessage(error, 'Dataset konnte nicht gesplittet werden.'),
      });
  }

  validate(): void {
    const dataset = this.facade.selectedDataset();
    if (!dataset || this.busy) return;
    this.busy = true;
    this.error = '';
    this.facade.validateDataset(dataset.id, idempotencyKey('dataset-validate')).pipe(finalize(() => this.busy = false)).subscribe({
      error: error => this.error = apiErrorMessage(error, 'Dataset-Validierung ist fehlgeschlagen.'),
    });
  }

  changeSplit(split: 'train' | 'validation'): void {
    const dataset = this.facade.selectedDataset();
    if (dataset) this.facade.loadRecords(dataset.id, split);
  }

  prompt(record: DatasetRecord): string {
    if (record.messages?.length) return boundedText(record.messages.map(message => `${message.role}: ${message.content}`).join('\n'), 1200);
    return boundedText([record.instruction, record.input].filter(Boolean).join('\n'), 1200);
  }
}
