import { Component, Input, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { ExplanationNoticeComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { ModelTrainingApiService } from '../model-training-api.service';
import {
  DendriticDryRunResult,
  DendriticExperimentRequest,
  DendriticMemoryCapability,
} from '../model-training.models';
import { apiErrorMessage, idempotencyKey } from '../model-training-status';

const DIGEST = /^[a-f0-9]{64}$/;

@Component({
  selector: 'app-dendritic-memory-workbench',
  standalone: true,
  imports: [ExplanationNoticeComponent, FormFieldComponent, FormsModule, SectionCardComponent],
  template: `
    <app-section-card
      title="Dendritic Memory Experiment"
      subtitle="Separater, default-off Forschungsjob; kein LoRA-Adapter und keine Leistungszusage."
      variant="warning">
      <div class="safety-labels" aria-label="Experimentstatus">
        <strong>experimental</strong><strong>not_production_ready</strong><strong>claims_not_verified</strong>
      </div>
      @if (!capability?.available) {
        <app-explanation-notice
          title="Experiment nicht verfügbar"
          [message]="capability?.reason_code || 'Der Hub hat keine dendritische Worker-Capability freigegeben.'"
          tone="warning" />
      } @else {
        <app-explanation-notice
          title="Vollautomatischer sicherer Pfad"
          message="Dry-run und Start benötigen keine menschliche Freigabe. Der Hub entscheidet deterministisch anhand von Policy, Capability und Limits."
          tone="technical" />
        <div class="grid">
          <app-form-field label="Dataset-Manifest SHA-256" [required]="true">
            <input [(ngModel)]="datasetDigest" maxlength="64" />
          </app-form-field>
          <app-form-field label="Lokales Basismodell" [required]="true">
            <input [(ngModel)]="baseModelId" maxlength="192" />
          </app-form-field>
          <app-form-field label="Modell-Snapshot SHA-256" [required]="true">
            <input [(ngModel)]="baseModelSnapshotDigest" maxlength="64" />
          </app-form-field>
          <app-form-field label="Target Layer" [required]="true">
            <input [(ngModel)]="targetLayer" maxlength="192" />
          </app-form-field>
          <app-form-field label="Branches">
            <input type="number" min="2" [max]="capability.limits?.max_branches || 64" [(ngModel)]="branchCount" />
          </app-form-field>
          <app-form-field label="Hidden Dimension">
            <input type="number" min="8" [max]="capability.limits?.max_hidden_dimension || 4096" [(ngModel)]="hiddenDimension" />
          </app-form-field>
          <app-form-field label="Top-k Routing">
            <input type="number" min="1" [max]="branchCount" [(ngModel)]="topK" />
          </app-form-field>
          <app-form-field label="Max Steps">
            <input type="number" min="1" [max]="capability.limits?.max_steps || 100000" [(ngModel)]="maxSteps" />
          </app-form-field>
        </div>
        <div class="actions">
          <button type="button" class="secondary" [disabled]="busy || !valid()" (click)="dryRun()">Dry-run</button>
          <button type="button" [disabled]="busy || !valid()" (click)="start()">Automatisch starten</button>
        </div>
        @if (dryRunResult) {
          <div class="result" [class.error]="!dryRunResult.admissible">
            {{ dryRunResult.admissible ? 'Hub-Admission bestanden' : dryRunResult.reason_codes.join(', ') }}
          </div>
        }
        @if (acceptedRunId) {
          <div class="result">Experimentlauf {{ acceptedRunId }} wurde vom Hub angenommen.</div>
        }
        @if (error) { <div class="result error" role="alert">{{ error }}</div> }
      }
    </app-section-card>
  `,
  styles: [`
    .safety-labels,.actions { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; }
    .safety-labels strong { border:1px solid var(--warning); border-radius:999px; padding:3px 8px; font-size:12px; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:12px; }
    .result { padding:10px; border:1px solid var(--success); border-radius:8px; }
    .result.error { border-color:var(--danger); color:var(--danger); }
    @media (max-width:700px) { .grid { grid-template-columns:1fr; } }
  `],
})
export class DendriticMemoryWorkbenchComponent {
  private readonly api = inject(ModelTrainingApiService);
  @Input({ required: true }) hubUrl = '';
  @Input({ required: true }) capability: DendriticMemoryCapability | null | undefined;

  datasetDigest = '';
  baseModelId = 'mock-local-model';
  baseModelSnapshotDigest = '';
  targetLayer = 'model.layers.0';
  branchCount = 4;
  hiddenDimension = 256;
  topK = 2;
  maxSteps = 100;
  busy = false;
  error = '';
  dryRunResult: DendriticDryRunResult | null = null;
  acceptedRunId = '';

  valid(): boolean {
    return Boolean(
      this.capability?.available
      && this.hubUrl
      && DIGEST.test(this.datasetDigest)
      && DIGEST.test(this.baseModelSnapshotDigest)
      && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(this.baseModelId)
      && /^[A-Za-z][A-Za-z0-9_.]{0,191}$/.test(this.targetLayer)
      && this.branchCount >= 2
      && this.branchCount <= Number(this.capability.limits?.max_branches || 64)
      && this.topK >= 1
      && this.topK <= this.branchCount
      && this.hiddenDimension >= 8
      && this.hiddenDimension <= Number(this.capability.limits?.max_hidden_dimension || 4096)
      && this.maxSteps >= 1
      && this.maxSteps <= Number(this.capability.limits?.max_steps || 100_000),
    );
  }

  dryRun(): void {
    if (!this.valid()) return;
    this.execute(() => this.api.dryRunDendriticExperiment(this.hubUrl, this.request()), result => {
      this.dryRunResult = result;
    });
  }

  start(): void {
    if (!this.valid()) return;
    this.execute(
      () => this.api.createDendriticExperiment(this.hubUrl, this.request(), idempotencyKey('dendritic-experiment')),
      result => { this.acceptedRunId = result.run_id; },
    );
  }

  private request(): DendriticExperimentRequest {
    return {
      spec: {
        schema: 'ananta.dendritic-memory-job.v1',
        spec_id: `experiment-${Date.now()}`,
        job_type: 'train_dendritic_memory',
        mode: 'dry_run',
        dataset_manifest_digest: this.datasetDigest,
        base_model_id: this.baseModelId.trim(),
        base_model_snapshot_digest: this.baseModelSnapshotDigest,
        configuration: {
          schema: 'ananta.dendritic-memory-config.v1',
          target_layers: [this.targetLayer.trim()],
          branch_count: Number(this.branchCount),
          hidden_dimension: Number(this.hiddenDimension),
          top_k: Number(this.topK),
          routing_enabled: true,
          readout: 'gated_residual',
          max_steps: Number(this.maxSteps),
          max_memory_bytes: Math.min(268_435_456, Number(this.capability?.limits?.max_pack_bytes || 268_435_456)),
          seed: 7,
          precision: 'float32',
          device_profile: 'cpu-safe',
          deterministic: true,
        },
        parent_pack_digests: [],
      },
    };
  }

  private execute<T>(operation: () => import('rxjs').Observable<T>, success: (value: T) => void): void {
    this.busy = true;
    this.error = '';
    operation().pipe(finalize(() => { this.busy = false; })).subscribe({
      next: success,
      error: error => { this.error = apiErrorMessage(error, 'Dendritischer Experimentlauf wurde abgelehnt.'); },
    });
  }
}
