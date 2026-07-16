import { Component, EventEmitter, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { ModelTrainingFacade } from '../model-training.facade';
import { DatasetDetail } from '../model-training.models';
import { apiErrorMessage, idempotencyKey } from '../model-training-status';

@Component({
  selector: 'app-model-training-dataset-upload',
  standalone: true,
  imports: [FormsModule, FormFieldComponent, SectionCardComponent],
  template: `
    <app-section-card
      title="Dataset importieren"
      subtitle="JSON oder JSONL wird ausschließlich an den Hub übertragen und dort geprüft."
      variant="technical">
      <div class="dataset-upload-grid">
        <app-form-field label="Dataset-Datei" [required]="true" [error]="fileError">
          <input
            data-testid="training-dataset-file"
            type="file"
            accept="application/json,application/x-ndjson,.json,.jsonl"
            (change)="selectFile($event)"
            [disabled]="busy"
            aria-describedby="training-dataset-file-hint" />
          <span id="training-dataset-file-hint" class="hint-text">Maximal {{ maxBytesLabel() }}; Inhalte werden nicht im Browser protokolliert.</span>
        </app-form-field>
        <app-form-field label="Name" hint="Optional; standardmäßig wird der Dateiname verwendet.">
          <input [(ngModel)]="name" [disabled]="busy" />
        </app-form-field>
        <app-form-field label="Zweck" [required]="true">
          <input [(ngModel)]="purpose" placeholder="z. B. lokale Codeassistenz" [disabled]="busy" />
        </app-form-field>
        <app-form-field label="Lizenz" [required]="true">
          <input [(ngModel)]="license" placeholder="z. B. private oder Apache-2.0" [disabled]="busy" />
        </app-form-field>
        <app-form-field label="Privacy-Klasse" [required]="true">
          <select [(ngModel)]="privacy" [disabled]="busy">
            <option value="private">privat</option>
            <option value="internal">intern</option>
            <option value="public">öffentlich</option>
          </select>
        </app-form-field>
        <app-form-field label="Validation-Anteil" hint="Deterministischer Split zwischen 5 % und 50 %.">
          <input type="number" min="0.05" max="0.5" step="0.05" [(ngModel)]="validationRatio" [disabled]="busy" />
        </app-form-field>
        <app-form-field label="Split-Seed">
          <input type="number" min="0" max="2147483647" step="1" [(ngModel)]="splitSeed" [disabled]="busy" />
        </app-form-field>
      </div>

      @if (file) {
        <p class="muted font-sm" role="status">Ausgewählt: {{ file.name }} · {{ fileSizeLabel() }}</p>
      }
      @if (busy) {
        <div class="upload-progress" role="status" aria-live="polite">
          <progress
            max="100"
            [attr.value]="uploadPercent"
            [attr.aria-label]="uploadPercent === null ? 'Dataset-Upload läuft' : 'Dataset-Upload ' + uploadPercent + ' Prozent'">
          </progress>
          <span>
            @if (uploadPercent !== null) { {{ uploadPercent }} % übertragen · }
            Upload und Hub-Prüfung laufen …
          </span>
        </div>
      }
      @if (error) {
        <div class="state-banner error" role="alert">
          {{ error }}
          @if (file && canUpload()) {
            <button type="button" class="secondary btn-small" (click)="upload()" [disabled]="busy">Erneut versuchen</button>
          }
        </div>
      }
      <button
        data-testid="training-dataset-upload"
        type="button"
        (click)="upload()"
        [disabled]="busy || !canUpload()">
        {{ busy ? 'Wird importiert …' : 'Dataset importieren' }}
      </button>
    </app-section-card>
  `,
  styles: [`
    .dataset-upload-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-bottom:12px; }
    .upload-progress { display:flex; align-items:center; gap:10px; margin:10px 0; }
    progress { width:min(360px,100%); }
    @media (max-width:700px) { .dataset-upload-grid { grid-template-columns:1fr; } }
  `],
})
export class DatasetUploadComponent {
  private readonly facade = inject(ModelTrainingFacade);
  @Output() uploaded = new EventEmitter<DatasetDetail>();

  file: File | null = null;
  name = '';
  purpose = '';
  license = '';
  privacy = 'private';
  validationRatio = 0.2;
  splitSeed = 42;
  busy = false;
  uploadPercent: number | null = null;
  error = '';
  fileError = '';

  selectFile(event: Event): void {
    this.file = (event.target as HTMLInputElement | null)?.files?.[0] || null;
    this.fileError = this.validateFile(this.file);
    this.error = '';
    this.uploadPercent = null;
  }

  canUpload(): boolean {
    return Boolean(
      this.file
      && !this.fileError
      && this.purpose.trim()
      && this.license.trim()
      && Number(this.validationRatio) >= 0.05
      && Number(this.validationRatio) <= 0.5
      && Number.isInteger(Number(this.splitSeed))
      && Number(this.splitSeed) >= 0,
    );
  }

  upload(): void {
    if (!this.file || !this.canUpload() || this.busy) return;
    this.busy = true;
    this.uploadPercent = 0;
    this.error = '';
    this.facade.uploadDatasetWithProgress({
      file: this.file,
      name: this.name,
      purpose: this.purpose,
      license: this.license,
      privacy: this.privacy,
      validation_ratio: Number(this.validationRatio),
      split_seed: Number(this.splitSeed),
    }, idempotencyKey('dataset-upload')).pipe(finalize(() => this.busy = false)).subscribe({
      next: event => {
        if (event.kind === 'progress') {
          this.uploadPercent = event.percent ?? null;
          return;
        }
        this.uploadPercent = 100;
        this.uploaded.emit(event.dataset);
        this.file = null;
        this.name = '';
      },
      error: error => {
        this.uploadPercent = null;
        this.error = apiErrorMessage(error, 'Dataset konnte nicht importiert werden.');
      },
    });
  }

  fileSizeLabel(): string {
    if (!this.file) return '-';
    return `${(this.file.size / 1024).toFixed(1)} KiB`;
  }

  maxBytesLabel(): string {
    const bytes = Number(this.facade.capabilities()?.limits?.max_dataset_bytes || 0);
    return bytes > 0 ? `${(bytes / (1024 * 1024)).toFixed(1)} MiB` : 'Hub-Limit';
  }

  private validateFile(file: File | null): string {
    if (!file) return 'Bitte eine JSON- oder JSONL-Datei wählen.';
    if (!/\.(json|jsonl)$/i.test(file.name)) return 'Nur .json und .jsonl sind erlaubt.';
    if (file.size <= 0) return 'Die Datei ist leer.';
    const maxBytes = Number(this.facade.capabilities()?.limits?.max_dataset_bytes || 0);
    if (maxBytes > 0 && file.size > maxBytes) return 'Die Datei überschreitet das vom Hub gemeldete Limit.';
    return '';
  }
}
