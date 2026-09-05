import { Component, EventEmitter, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { FormFieldComponent, WizardShellComponent, WizardStep } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { ExplanationNoticeComponent } from '../../../shared/ui/display';
import { ModelTrainingFacade } from '../model-training.facade';
import {
  CreateTrainingJobRequest,
  DatasetSummary,
  TrainingBackendCapability,
  TrainingBackendRecommendation,
  TrainingJobAcceptance,
} from '../model-training.models';
import { apiErrorMessage, idempotencyKey } from '../model-training-status';

const WIZARD_STEPS: WizardStep[] = [
  { id: 'dataset', title: 'Dataset', helper: 'Trainierbares Dataset und Validation-Split auswählen.' },
  { id: 'runtime', title: 'Runtime', helper: 'Basismodell, Backend, Methode und GPU-Profil festlegen.' },
  { id: 'parameters', title: 'Parameter', helper: 'Bounded LoRA-/QLoRA-Hyperparameter konfigurieren.' },
  { id: 'review', title: 'Prüfen', helper: 'Effektiven Hub-Auftrag prüfen und explizit absenden.' },
];

@Component({
  selector: 'app-model-training-wizard',
  standalone: true,
  imports: [FormsModule, ExplanationNoticeComponent, FormFieldComponent, SectionCardComponent, WizardShellComponent],
  template: `
    <app-section-card
      title="LoRA-/QLoRA-Job anlegen"
      subtitle="Der Hub übernimmt Admission, Queue, Approval und Delegation; der Browser wartet nicht auf das Training."
      variant="warning">
      <app-wizard-shell
        [steps]="steps"
        [activeIndex]="activeIndex"
        [canContinue]="canContinue()"
        [busy]="busy"
        title="Trainingskonfiguration"
        submitLabel="Job in Hub-Queue einstellen"
        busyLabel="Job wird angenommen …"
        (stepSelect)="selectStep($event)"
        (previous)="previous()"
        (next)="next()"
        (submitRequested)="submit()">

        @switch (steps[activeIndex].id) {
          @case ('dataset') {
            <app-form-field label="Dataset" [required]="true" [error]="datasetError()">
              <select [(ngModel)]="datasetId" data-testid="training-wizard-dataset">
                <option value="">Bitte wählen</option>
                @for (dataset of facade.datasets(); track dataset.id) {
                  <option [value]="dataset.id">{{ dataset.name }} · {{ dataset.train_record_count }}/{{ dataset.validation_record_count }} · {{ dataset.validation_status || dataset.status }}</option>
                }
              </select>
            </app-form-field>
            @if (!facade.datasets().length) {
              <app-explanation-notice title="Kein Dataset verfügbar" message="Importieren, splitten und validieren Sie zuerst ein Dataset." tone="warning" />
            }
          }
          @case ('runtime') {
            <div class="wizard-grid">
              <app-form-field label="Modus">
                <select [(ngModel)]="mode" (ngModelChange)="onModeChange()">
                  <option value="dry_run">Dry-run</option>
                  <option value="live">Live</option>
                </select>
              </app-form-field>
              <app-form-field label="Methode">
                <select [(ngModel)]="method">
                  <option value="lora">LoRA</option>
                  <option value="qlora">QLoRA (4-bit)</option>
                </select>
              </app-form-field>
              <app-form-field label="Basismodell" [required]="true">
                <select data-testid="training-wizard-base-model" [(ngModel)]="baseModelId" (ngModelChange)="ensureRuntimeCompatibility()">
                  <option value="">Bitte wählen</option>
                  @for (model of facade.capabilities()?.base_models || []; track model.id) {
                    <option [value]="model.id" [disabled]="model.available === false">{{ model.label || model.id }}{{ model.local ? ' · lokal' : '' }}</option>
                  }
                </select>
              </app-form-field>
              <app-form-field label="Backend" [required]="true">
                <select data-testid="training-wizard-backend" [(ngModel)]="backend">
                  <option value="">Bitte wählen</option>
                  @for (item of facade.capabilities()?.backends || []; track item.id) {
                    <option [value]="item.id" [disabled]="!item.available">{{ item.id }}{{ item.available ? '' : ' · ' + (item.reason_code || 'nicht verfügbar') }}</option>
                  }
                </select>
              </app-form-field>
              <div class="backend-comparison">
                <button type="button" class="btn secondary" (click)="requestRecommendation()" [disabled]="!gpuProfile || recommendationBusy">
                  {{ recommendationBusy ? 'Hub prüft …' : 'Hub-Empfehlung prüfen' }}
                </button>
                @if (selectedBackend(); as selected) {
                  <span>{{ selected.version || 'verwaltet' }} · {{ selected.license_spdx || 'Lizenzregister' }} · {{ selected.maturity || 'unbekannt' }}</span>
                  @if (selected.maintenance === 'unmaintained') {
                    <strong class="backend-warning">Upstream nicht mehr gewartet · nur experimental/default-off</strong>
                  }
                }
                @if (recommendation) {
                  <span>Empfehlung: <strong>{{ recommendation.backend }}</strong> · Schätzung, keine automatische Auswahl</span>
                }
              </div>
              <app-form-field label="GPU-Profil" [required]="true">
                <select data-testid="training-wizard-gpu-profile" [(ngModel)]="gpuProfile" (ngModelChange)="onGpuProfileChange()">
                  <option value="">Bitte wählen</option>
                  @for (profile of facade.capabilities()?.gpu_profiles || []; track profile.id) {
                    <option [value]="profile.id" [disabled]="mode === 'live' && !profile.available">{{ profile.label || profile.id }}{{ profile.available ? '' : ' · nicht verfügbar' }}</option>
                  }
                </select>
              </app-form-field>
              <app-form-field label="Adaptername" [required]="true">
                <input [(ngModel)]="outputName" maxlength="96" placeholder="mein-lora-adapter" />
              </app-form-field>
            </div>
          }
          @case ('parameters') {
            <div class="wizard-grid">
              <app-form-field label="LoRA Rank" [error]="rankError()">
                <input type="number" min="1" [max]="maxRank()" [(ngModel)]="loraRank" />
              </app-form-field>
              <app-form-field label="LoRA Alpha">
                <input type="number" min="1" [max]="maxAlpha()" [(ngModel)]="loraAlpha" />
              </app-form-field>
              <app-form-field label="Dropout">
                <input type="number" min="0" max="0.5" step="0.01" [(ngModel)]="loraDropout" />
              </app-form-field>
              <app-form-field label="Learning Rate">
                <input type="number" min="0.0000001" max="0.1" step="0.000001" [(ngModel)]="learningRate" />
              </app-form-field>
              <app-form-field label="Batchgröße">
                <input type="number" min="1" [max]="maxBatchSize()" [(ngModel)]="batchSize" />
              </app-form-field>
              <app-form-field label="Gradient Accumulation">
                <input type="number" min="1" [max]="maxGradientAccumulation()" [(ngModel)]="gradientAccumulation" />
              </app-form-field>
              <app-form-field label="Max Steps" [error]="stepsError()">
                <input type="number" min="1" [max]="maxSteps()" [(ngModel)]="maxTrainingSteps" />
              </app-form-field>
              <app-form-field label="Max Sequenzlänge">
                <input type="number" [min]="minSequenceLength()" [max]="maxSequenceLengthLimit()" step="128" [(ngModel)]="maxSequenceLength" />
              </app-form-field>
            </div>
          }
          @case ('review') {
            <div class="review-grid">
              <div><span>Dataset</span><strong>{{ selectedDataset()?.name || datasetId }}</strong></div>
              <div><span>Modell</span><strong>{{ baseModelId }}</strong></div>
              <div><span>Ausführung</span><strong>{{ mode }} · {{ method }} · {{ backend }}</strong></div>
              <div><span>GPU</span><strong>{{ gpuProfile }}</strong></div>
              <div><span>LoRA</span><strong>r={{ loraRank }}, alpha={{ loraAlpha }}, dropout={{ loraDropout }}</strong></div>
              <div><span>Training</span><strong>{{ maxTrainingSteps }} Steps · LR {{ learningRate }}</strong></div>
            </div>
            @if (blockingReasons().length) {
              <div class="state-banner error" role="alert">
                <strong>Live-Training blockiert</strong>
                <ul>@for (reason of blockingReasons(); track reason) { <li>{{ reason }}</li> }</ul>
              </div>
            }
            @if (mode === 'live') {
              <app-form-field label="Begründung für Live-Ausführung" [required]="true">
                <textarea [(ngModel)]="riskReason" rows="3" maxlength="500"></textarea>
              </app-form-field>
              <label class="confirmation">
                <input type="checkbox" [(ngModel)]="liveConfirmed" />
                Ich bestätige GPU-/Shell-Ausführung, Artefaktschreibzugriffe und das erforderliche Hub-Approval.
              </label>
            } @else {
              <app-explanation-notice title="Sicherer Standard" message="Dry-run prüft Admission und Konfiguration, ohne einen Live-GPU-Lauf zu behaupten." tone="technical" />
            }
          }
        }
        @if (error) { <div class="state-banner error" role="alert">{{ error }}</div> }
      </app-wizard-shell>
    </app-section-card>
  `,
  styles: [`
    .wizard-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    .review-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-bottom:14px; }
    .review-grid div { display:grid; gap:4px; padding:10px; border:1px solid var(--border); border-radius:8px; }
    .review-grid span { color:var(--muted); font-size:12px; }
    .confirmation { display:flex; gap:8px; align-items:flex-start; margin-top:12px; }
    .backend-comparison { display:grid; gap:6px; align-content:start; font-size:12px; color:var(--muted); }
    .backend-warning { color:var(--warning); }
    @media (max-width:700px) { .wizard-grid,.review-grid { grid-template-columns:1fr; } }
  `],
})
export class TrainingWizardComponent {
  readonly facade = inject(ModelTrainingFacade);
  @Output() jobCreated = new EventEmitter<TrainingJobAcceptance>();
  readonly steps = WIZARD_STEPS;
  activeIndex = 0;
  busy = false;
  error = '';

  datasetId = '';
  mode: 'dry_run' | 'live' = 'dry_run';
  method: 'lora' | 'qlora' = 'qlora';
  baseModelId = '';
  backend = '';
  gpuProfile = '';
  outputName = 'ananta-lora-adapter';
  loraRank = 16;
  loraAlpha = 32;
  loraDropout = 0.05;
  learningRate = 0.0002;
  batchSize = 1;
  gradientAccumulation = 8;
  maxTrainingSteps = 100;
  maxSequenceLength = 2048;
  riskReason = '';
  liveConfirmed = false;
  recommendation: TrainingBackendRecommendation | null = null;
  recommendationBusy = false;

  selectedDataset(): DatasetSummary | null {
    return this.facade.datasets().find(dataset => dataset.id === this.datasetId) || null;
  }

  datasetError(): string {
    if (!this.datasetId) return '';
    const dataset = this.selectedDataset();
    if (!dataset) return 'Dataset ist nicht mehr im Hub-Katalog vorhanden.';
    if (this.mode === 'live' && !this.datasetTrainable(dataset)) return 'Für Live-Training sind gültige Train- und Validation-Splits erforderlich.';
    return '';
  }

  rankError(): string {
    return Number(this.loraRank) >= 1 && Number(this.loraRank) <= this.maxRank() ? '' : `Rank muss zwischen 1 und ${this.maxRank()} liegen.`;
  }

  stepsError(): string {
    return Number(this.maxTrainingSteps) >= 1 && Number(this.maxTrainingSteps) <= this.maxSteps() ? '' : `Max Steps muss zwischen 1 und ${this.maxSteps()} liegen.`;
  }

  maxRank(): number { return Number(this.facade.capabilities()?.limits?.max_lora_rank || 256); }
  maxAlpha(): number { return Number(this.facade.capabilities()?.limits?.max_lora_alpha || 512); }
  maxBatchSize(): number {
    const globalLimit = Number(this.facade.capabilities()?.limits?.max_batch_size || 128);
    const profileLimit = Number(this.selectedGpuProfile()?.max_batch_size || globalLimit);
    return Math.min(globalLimit, profileLimit);
  }
  maxGradientAccumulation(): number { return Number(this.facade.capabilities()?.limits?.max_gradient_accumulation_steps || 1024); }
  minSequenceLength(): number { return Number(this.facade.capabilities()?.limits?.min_sequence_length || 128); }
  maxSequenceLengthLimit(): number {
    const globalLimit = Number(this.facade.capabilities()?.limits?.max_sequence_length || 32_768);
    const profileLimit = Number(this.selectedGpuProfile()?.max_sequence_length || globalLimit);
    return Math.min(globalLimit, profileLimit);
  }
  maxSteps(): number { return Number(this.facade.capabilities()?.limits?.max_steps || 100_000); }

  blockingReasons(): string[] {
    if (this.mode !== 'live') return [];
    const reasons: string[] = [];
    const dataset = this.selectedDataset();
    if (!dataset || !this.datasetTrainable(dataset)) reasons.push('Dataset ist nicht validiert oder besitzt keinen Validation-Split.');
    if (!this.facade.capabilities()?.available) reasons.push(`Training-Runtime ist nicht verfügbar (${this.facade.capabilities()?.reason_code || 'unbekannter Grund'}).`);
    if (!this.runtimeCompatible()) reasons.push('Basismodell, Backend oder GPU-Profil sind nicht kompatibel/verfügbar.');
    return reasons;
  }

  canContinue(): boolean {
    const step = this.steps[this.activeIndex]?.id;
    if (step === 'dataset') return Boolean(this.datasetId && this.selectedDataset());
    if (step === 'runtime') return Boolean(
      this.baseModelId && this.backend && this.gpuProfile && this.outputName.trim()
      && this.runtimeCompatible()
      && (this.mode === 'live' || (this.backend === 'mock' && this.gpuProfile === 'none')),
    );
    if (step === 'parameters') return !this.rankError() && !this.stepsError()
      && Number.isFinite(this.loraAlpha) && this.loraAlpha >= 1 && this.loraAlpha <= this.maxAlpha()
      && Number.isFinite(this.loraDropout) && this.loraDropout >= 0 && this.loraDropout <= 0.5
      && Number.isFinite(this.learningRate) && this.learningRate > 0 && this.learningRate <= 0.1
      && Number.isFinite(this.batchSize) && this.batchSize >= 1 && this.batchSize <= this.maxBatchSize()
      && Number.isFinite(this.gradientAccumulation) && this.gradientAccumulation >= 1
      && this.gradientAccumulation <= this.maxGradientAccumulation()
      && Number.isFinite(this.maxSequenceLength) && this.maxSequenceLength >= this.minSequenceLength()
      && this.maxSequenceLength <= this.maxSequenceLengthLimit();
    if (step === 'review') return this.blockingReasons().length === 0
      && (this.mode === 'dry_run' || (this.liveConfirmed && this.riskReason.trim().length >= 8));
    return false;
  }

  selectStep(index: number): void {
    if (index <= this.activeIndex) this.activeIndex = Math.max(0, index);
  }

  previous(): void { this.activeIndex = Math.max(0, this.activeIndex - 1); }
  next(): void { if (this.canContinue()) this.activeIndex = Math.min(this.steps.length - 1, this.activeIndex + 1); }

  onModeChange(): void {
    if (this.mode === 'dry_run') {
      this.liveConfirmed = false;
      this.riskReason = '';
      if (this.facade.capabilities()?.backends.some(item => item.id === 'mock' && item.available)) this.backend = 'mock';
      if (this.facade.capabilities()?.gpu_profiles.some(item => item.id === 'none')) this.gpuProfile = 'none';
      this.onGpuProfileChange();
    }
  }

  onGpuProfileChange(): void {
    this.batchSize = Math.min(Number(this.batchSize), this.maxBatchSize());
    this.maxSequenceLength = Math.min(Number(this.maxSequenceLength), this.maxSequenceLengthLimit());
  }

  ensureRuntimeCompatibility(): void {
    const model = this.facade.capabilities()?.base_models.find(item => item.id === this.baseModelId);
    if (model && this.backend && !model.compatible_backends.includes(this.backend)) this.backend = '';
  }

  selectedBackend(): TrainingBackendCapability | null {
    return this.facade.capabilities()?.backends.find(item => item.id === this.backend) || null;
  }

  requestRecommendation(): void {
    if (!this.gpuProfile || this.recommendationBusy) return;
    this.recommendationBusy = true;
    this.recommendation = null;
    this.facade.recommendBackend({
      objective: 'sft',
      method: this.method,
      modality: 'text',
      resource_profile: this.gpuProfile === 'none' ? 'cpu' : this.gpuProfile,
      estimated_model_bytes: 0,
      runtime_budget_seconds: 3600,
      export_format: 'adapter',
    }).pipe(finalize(() => this.recommendationBusy = false)).subscribe({
      next: recommendation => this.recommendation = recommendation,
      error: error => this.error = apiErrorMessage(error, 'Backend-Empfehlung ist nicht verfügbar.'),
    });
  }

  submit(): void {
    if (this.busy || this.activeIndex !== this.steps.length - 1 || !this.canContinue()) return;
    const payload: CreateTrainingJobRequest = {
      dataset_id: this.datasetId,
      base_model_id: this.baseModelId,
      backend: this.backend,
      mode: this.mode,
      gpu_profile: this.gpuProfile,
      method: this.method,
      output_name: this.outputName.trim(),
      hyperparameters: {
        lora_rank: Number(this.loraRank),
        lora_alpha: Number(this.loraAlpha),
        lora_dropout: Number(this.loraDropout),
        learning_rate: Number(this.learningRate),
        batch_size: Number(this.batchSize),
        gradient_accumulation_steps: Number(this.gradientAccumulation),
        max_steps: Number(this.maxTrainingSteps),
        max_sequence_length: Number(this.maxSequenceLength),
        quantization: this.method === 'qlora' ? '4bit' : 'none',
      },
      require_dataset_validation: true,
      require_secret_scan: true,
      risk_reason: this.mode === 'live' ? this.riskReason.trim() : undefined,
      live_confirmed: this.mode === 'live' ? true : undefined,
    };
    this.busy = true;
    this.error = '';
    this.facade.createJob(payload, idempotencyKey('training-job')).pipe(finalize(() => this.busy = false)).subscribe({
      next: accepted => this.jobCreated.emit(accepted),
      error: error => this.error = apiErrorMessage(error, 'Trainingsjob konnte nicht angelegt werden.'),
    });
  }

  private datasetTrainable(dataset: DatasetSummary): boolean {
    return Boolean(
      (dataset.trainable === true || dataset.validation_status === 'valid' || dataset.status === 'valid')
      && dataset.train_record_count > 0
      && dataset.validation_record_count > 0,
    );
  }

  private runtimeCompatible(): boolean {
    const capabilities = this.facade.capabilities();
    const model = capabilities?.base_models.find(item => item.id === this.baseModelId);
    const backend = capabilities?.backends.find(item => item.id === this.backend);
    const gpu = capabilities?.gpu_profiles.find(item => item.id === this.gpuProfile);
    return Boolean(capabilities?.available && model && model.available !== false && model.compatible_backends.includes(this.backend) && backend?.available && gpu?.available);
  }

  private selectedGpuProfile() {
    return this.facade.capabilities()?.gpu_profiles.find(item => item.id === this.gpuProfile);
  }
}
