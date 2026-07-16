import { Component, effect, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { ExplanationNoticeComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { StatusBadgeComponent } from '../../../shared/ui/state';
import { ModelTrainingFacade } from '../model-training.facade';
import { DatasetSummary } from '../model-training.models';
import { apiErrorMessage, idempotencyKey } from '../model-training-status';

@Component({
  selector: 'app-model-training-dataset-management',
  standalone: true,
  imports: [ExplanationNoticeComponent, FormsModule, FormFieldComponent, SectionCardComponent, StatusBadgeComponent],
  template: `
    @if (facade.selectedDataset(); as dataset) {
      <app-section-card
        title="Externes Validation-Dataset & Lifecycle"
        subtitle="Separate Validation anbinden oder das ausgewählte Dataset kontrolliert löschen."
        variant="warning">
        <div class="management-grid">
          <section class="management-panel" aria-labelledby="external-validation-title">
            <h3 id="external-validation-title">Separates Validation-Dataset anhängen</h3>
            <p class="muted">
              Der Hub validiert beide Quellen, prüft semantische Überschneidungen und bindet die Validation unveränderlich an das Trainingsdataset.
            </p>
            @if (dataset.external_validation; as binding) {
              <div class="binding-status" role="status">
                <app-status-badge label="Extern gebunden" tone="success" [dot]="true" />
                <span>{{ binding.dataset_id }} · {{ binding.algorithm_version }} · Overlap {{ binding.semantic_overlap_count }}</span>
              </div>
            }
            <app-form-field label="Separat hochgeladenes Validation-Dataset" [required]="true">
              <select [(ngModel)]="validationDatasetId" [disabled]="attachBusy">
                <option value="">Bitte wählen</option>
                @for (candidate of validationCandidates(); track candidate.id) {
                  <option [value]="candidate.id">
                    {{ candidate.name }} · {{ candidate.record_count }} Records · {{ candidate.validation_status || candidate.status }}
                  </option>
                }
              </select>
            </app-form-field>
            <label class="confirmation">
              <input type="checkbox" [(ngModel)]="attachConfirmed" [disabled]="attachBusy" />
              Ich bestätige, dass der bestehende Validation-Split ersetzt werden darf und der Hub die Paarprüfung ausführt.
            </label>
            <button
              type="button"
              (click)="attachValidationDataset()"
              [disabled]="attachBusy || !validationDatasetId || !attachConfirmed">
              {{ attachBusy ? 'Validation wird angebunden …' : 'Validation-Dataset anhängen' }}
            </button>
            @if (attachMessage) { <div class="state-banner success" role="status">{{ attachMessage }}</div> }
            @if (attachError) { <div class="state-banner error" role="alert">{{ attachError }}</div> }
          </section>

          <section class="management-panel danger-panel" aria-labelledby="dataset-delete-title">
            <h3 id="dataset-delete-title">Dataset löschen</h3>
            <app-explanation-notice
              title="Kein Force-Delete"
              message="Referenzierte Datasets werden vom Hub mit dataset_referenced (409) blockiert. Die UI umgeht diese Schutzregel nicht."
              tone="warning" />
            <p><strong>{{ dataset.name }}</strong> ({{ dataset.id }}) wird einschließlich seiner gespeicherten Dataset-Artefakte gelöscht.</p>
            <label class="confirmation">
              <input type="checkbox" [(ngModel)]="deleteConfirmed" [disabled]="deleteBusy" />
              Ich bestätige die dauerhafte Löschung dieses nicht mehr benötigten Datasets.
            </label>
            <button
              type="button"
              class="danger-button"
              (click)="deleteDataset()"
              [disabled]="deleteBusy || !deleteConfirmed">
              {{ deleteBusy ? 'Dataset wird gelöscht …' : 'Dataset endgültig löschen' }}
            </button>
            @if (deleteError) { <div class="state-banner error" role="alert">{{ deleteError }}</div> }
          </section>
        </div>
      </app-section-card>
    }
  `,
  styles: [`
    .management-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
    .management-panel { display:grid; gap:12px; align-content:start; padding:14px; border:1px solid var(--border); border-radius:10px; }
    .management-panel h3,.management-panel p { margin:0; }
    .danger-panel { border-color:var(--tone-error); }
    .binding-status { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .confirmation { display:flex; gap:8px; align-items:flex-start; }
    .danger-button { background:var(--tone-error-text); border-color:var(--tone-error-text); color:#fff; }
    @media (max-width:800px) { .management-grid { grid-template-columns:1fr; } }
  `],
})
export class DatasetManagementComponent {
  readonly facade = inject(ModelTrainingFacade);
  validationDatasetId = '';
  attachConfirmed = false;
  attachBusy = false;
  attachError = '';
  attachMessage = '';
  deleteConfirmed = false;
  deleteBusy = false;
  deleteError = '';
  private activeDatasetId = '';

  constructor() {
    effect(() => {
      const datasetId = this.facade.selectedDataset()?.id || '';
      if (datasetId === this.activeDatasetId) return;
      this.activeDatasetId = datasetId;
      this.validationDatasetId = '';
      this.attachConfirmed = false;
      this.attachError = '';
      this.attachMessage = '';
      this.deleteConfirmed = false;
      this.deleteError = '';
    });
  }

  validationCandidates(): DatasetSummary[] {
    const selectedId = this.facade.selectedDataset()?.id;
    return this.facade.datasets().filter(dataset => dataset.id !== selectedId && dataset.record_count > 0);
  }

  attachValidationDataset(): void {
    const dataset = this.facade.selectedDataset();
    if (!dataset || !this.validationDatasetId || !this.attachConfirmed || this.attachBusy) return;
    this.attachBusy = true;
    this.attachError = '';
    this.attachMessage = '';
    this.facade.attachValidationDataset(
      dataset.id,
      this.validationDatasetId,
      idempotencyKey('dataset-external-validation'),
    ).pipe(finalize(() => this.attachBusy = false)).subscribe({
      next: result => {
        this.attachConfirmed = false;
        this.attachMessage = `Validation-Dataset ${result.external_validation?.dataset_id || this.validationDatasetId} wurde leak-geprüft angebunden.`;
      },
      error: error => this.attachError = apiErrorMessage(error, 'Validation-Dataset konnte nicht angebunden werden.'),
    });
  }

  deleteDataset(): void {
    const dataset = this.facade.selectedDataset();
    if (!dataset || !this.deleteConfirmed || this.deleteBusy) return;
    this.deleteBusy = true;
    this.deleteError = '';
    this.facade.deleteDataset(dataset.id, idempotencyKey('dataset-delete')).pipe(finalize(() => this.deleteBusy = false)).subscribe({
      error: error => this.deleteError = apiErrorMessage(error, 'Dataset konnte nicht gelöscht werden.'),
    });
  }
}
