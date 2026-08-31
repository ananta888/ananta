import { JsonPipe } from '@angular/common';
import { ChangeDetectorRef, Component, Input, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize, forkJoin } from 'rxjs';

import { ExplanationNoticeComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { ModelTrainingApiService } from '../model-training-api.service';
import {
  DendriticDryRunResult,
  DendriticExperimentRequest,
  DendriticMemoryCapability,
  DendriticPackSummary,
  DendriticRunDetail,
} from '../model-training.models';
import { apiErrorMessage, idempotencyKey } from '../model-training-status';

const DIGEST = /^[a-f0-9]{64}$/;

@Component({
  selector: 'app-dendritic-memory-workbench',
  standalone: true,
  imports: [ExplanationNoticeComponent, FormFieldComponent, FormsModule, JsonPipe, SectionCardComponent],
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
        <div class="actions">
          <button type="button" class="secondary" [disabled]="busy" (click)="refresh()">Jobs und Packs aktualisieren</button>
        </div>
        <section aria-label="Dendritic Jobmonitor">
          <h3>Experiment-Jobs <small>experimental</small></h3>
          @for (run of runs; track run.run_id) {
            <article class="record">
              <strong>{{ run.run_id }}</strong>
              <span>{{ run.state }} · {{ run.reason_code }}</span>
              <span>Revision {{ run.revision }} · Events {{ run.result?.event_count ?? '—' }}</span>
              @if (run.state === 'queued' || run.state === 'retry_queued' || run.state === 'running') {
                <button type="button" class="secondary" [disabled]="busy" (click)="cancel(run)">Automatisch abbrechen</button>
              }
              @if (run.result?.output; as report) {
                <details><summary>Vergleichsreport</summary><pre>{{ report | json }}</pre></details>
              }
            </article>
          } @empty { <p>Noch keine Experiment-Jobs.</p> }
        </section>
        <section aria-label="Dendritic Memory Packs">
          <h3>Memory Packs <small>nicht produktionsfähig</small></h3>
          @for (pack of packs; track pack.pack_digest) {
            <article class="record">
              <strong>{{ pack.pack_digest }}</strong>
              <span>{{ pack.state }} · {{ pack.manifest.base_model_id }}</span>
              <span>Parents: {{ pack.manifest.parent_pack_digests.join(' → ') || 'keine' }}</span>
              <span>Targets: {{ pack.manifest.target_layers.join(', ') }}</span>
              @if (pack.state === 'approved_for_experiment') {
                <button type="button" class="secondary" [disabled]="busy" (click)="revoke(pack)">Experiment-Pack widerrufen</button>
              }
            </article>
          } @empty { <p>Noch keine Memory Packs.</p> }
          <p class="warning">Produktiv aktivieren ist für experimentelle Memory Packs nicht verfügbar.</p>
        </section>
      }
    </app-section-card>
  `,
  styles: [`
    .safety-labels,.actions { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; }
    .safety-labels strong { border:1px solid var(--warning); border-radius:999px; padding:3px 8px; font-size:12px; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:12px; }
    .result,.record { padding:10px; border:1px solid var(--success); border-radius:8px; }
    .result.error { border-color:var(--danger); color:var(--danger); }
    .record { display:grid; gap:5px; margin:8px 0; overflow-wrap:anywhere; }
    pre { white-space:pre-wrap; max-height:240px; overflow:auto; }
    @media (max-width:700px) { .grid { grid-template-columns:1fr; } }
  `],
})
export class DendriticMemoryWorkbenchComponent {
  private readonly api = inject(ModelTrainingApiService);
  private readonly changes = inject(ChangeDetectorRef);
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
  runs: DendriticRunDetail[] = [];
  packs: DendriticPackSummary[] = [];

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
    this.execute(() => this.api.dryRunDendriticExperiment(this.hubUrl, this.request('dry_run')), result => {
      this.dryRunResult = result;
    });
  }

  start(): void {
    if (!this.valid()) return;
    this.execute(
      () => this.api.createDendriticExperiment(this.hubUrl, this.request('live'), idempotencyKey('dendritic-experiment')),
      result => { this.acceptedRunId = result.run_id; queueMicrotask(() => this.refresh()); },
    );
  }

  refresh(): void {
    if (!this.hubUrl || this.busy) return;
    this.execute(
      () => forkJoin({ runs: this.api.listDendriticRuns(this.hubUrl), packs: this.api.listDendriticPacks(this.hubUrl) }),
      value => { this.runs = value.runs.items; this.packs = value.packs.items; },
    );
  }

  cancel(run: DendriticRunDetail): void {
    this.execute(
      () => this.api.cancelDendriticRun(this.hubUrl, run.run_id, run.revision),
      () => queueMicrotask(() => this.refresh()),
    );
  }

  revoke(pack: DendriticPackSummary): void {
    this.execute(
      () => this.api.revokeDendriticPack(this.hubUrl, pack, idempotencyKey('dendritic-revoke')),
      () => queueMicrotask(() => this.refresh()),
    );
  }

  private request(mode: 'dry_run' | 'live'): DendriticExperimentRequest {
    return {
      spec: {
        schema: 'ananta.dendritic-memory-job.v1',
        spec_id: `experiment-${Date.now()}`,
        job_type: 'train_dendritic_memory',
        mode,
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
    operation().pipe(finalize(() => {
      this.busy = false;
      this.changes.markForCheck();
    })).subscribe({
      next: value => {
        success(value);
        this.changes.markForCheck();
      },
      error: error => {
        this.error = apiErrorMessage(error, 'Dendritischer Experimentlauf wurde abgelehnt.');
        this.changes.markForCheck();
      },
    });
  }
}
