import { Component, Input, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize, switchMap } from 'rxjs';

import { ExplanationNoticeComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { ModelTrainingApiService } from '../model-training-api.service';
import {
  ResearchRecipeRequest,
  ResearchResolvedRecipe,
  ResearchStage,
  ResearchTrainingCapability,
  ResearchTrainingPreflight,
  ResearchTrainingRecipe,
  ResearchTrainingRequest,
} from '../model-training.models';
import { apiErrorMessage, idempotencyKey } from '../model-training-status';

const DIGEST = /^[a-f0-9]{64}$/;

@Component({
  selector: 'app-research-training-workbench',
  standalone: true,
  imports: [ExplanationNoticeComponent, FormFieldComponent, FormsModule, SectionCardComponent],
  template: `
    <app-section-card
      title="Full Model Research Training"
      subtitle="Default-off Forschungs-DAG; getrennt von LoRA und nicht produktionsbereit."
      variant="warning">
      <div class="labels" aria-label="Research-Status">
        <strong>experimental</strong><strong>not_production_ready</strong><strong>claims_not_verified</strong>
      </div>
      <app-explanation-notice
        title="Vollautomatischer Hub-Pfad"
        message="Recipe, Preflight, Stage-DAG und Release-Gates laufen ohne menschliche Eingabe. Unzulässige Runs enden mit einem begrenzten Reason-Code."
        tone="technical" />
      <div class="grid">
        <app-form-field label="Dataset-Manifest SHA-256" [required]="true">
          <input [(ngModel)]="datasetDigest" maxlength="64" />
        </app-form-field>
        <app-form-field label="Source-Revision SHA-256" [required]="true">
          <input [(ngModel)]="sourceRevisionDigest" maxlength="64" />
        </app-form-field>
        <app-form-field label="Modellfamilie" [required]="true">
          <input [(ngModel)]="modelFamily" maxlength="128" />
        </app-form-field>
        <app-form-field label="Architektur" [required]="true">
          <input [(ngModel)]="architecture" maxlength="128" />
        </app-form-field>
        <app-form-field label="Depth"><input type="number" min="1" max="128" [(ngModel)]="depth" /></app-form-field>
        <app-form-field label="Context"><input type="number" min="128" max="262144" [(ngModel)]="contextLength" /></app-form-field>
        <app-form-field label="Vokabular"><input type="number" min="256" max="1048576" [(ngModel)]="vocabSize" /></app-form-field>
        <app-form-field label="Max Steps"><input type="number" min="1" max="100000000" [(ngModel)]="maxSteps" /></app-form-field>
      </div>
      <div class="actions">
        <button type="button" class="secondary" [disabled]="busy || !valid()" (click)="dryRun()">Recipe + Dry-run</button>
        <button type="button" [disabled]="busy || !valid()" (click)="start()">Automatisch starten</button>
      </div>
      @if (resolved) {
        <div class="result">{{ resolved.resolved_hyperparameters['num_layers'] }} Layer · {{ resolved.resolved_hyperparameters['hidden_size'] }} Hidden · Digest {{ resolved.recipe_digest }}</div>
      }
      @if (preflight) {
        <div class="result" [class.error]="!preflight.admissible">
          {{ preflight.admissible ? 'Hub-Admission bestanden' : preflight.reason_codes.join(', ') }}
        </div>
      }
      @if (acceptedRunId) { <div class="result">Research Run {{ acceptedRunId }} wurde angenommen.</div> }
      @if (error) { <div class="result error" role="alert">{{ error }}</div> }
    </app-section-card>
  `,
  styles: [`
    .labels,.actions { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; }
    .labels strong { border:1px solid var(--warning); border-radius:999px; padding:3px 8px; font-size:12px; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:12px; }
    .result { padding:10px; border:1px solid var(--success); border-radius:8px; overflow-wrap:anywhere; }
    .result.error { border-color:var(--danger); color:var(--danger); }
    @media (max-width:700px) { .grid { grid-template-columns:1fr; } }
  `],
})
export class ResearchTrainingWorkbenchComponent {
  private readonly api = inject(ModelTrainingApiService);
  @Input({ required: true }) hubUrl = '';
  @Input({ required: true }) capability: ResearchTrainingCapability | null | undefined;

  datasetDigest = '';
  sourceRevisionDigest = '';
  modelFamily = 'tiny-local';
  architecture = 'decoder-transformer';
  depth = 6;
  contextLength = 1024;
  vocabSize = 8192;
  maxSteps = 100;
  busy = false;
  error = '';
  resolved: ResearchResolvedRecipe | null = null;
  preflight: ResearchTrainingPreflight | null = null;
  acceptedRunId = '';

  valid(): boolean {
    return Boolean(
      this.capability?.available && this.hubUrl && DIGEST.test(this.datasetDigest)
      && DIGEST.test(this.sourceRevisionDigest) && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(this.modelFamily)
      && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(this.architecture)
      && this.depth >= 1 && this.depth <= 128 && this.contextLength >= 128 && this.contextLength <= 262_144
      && this.vocabSize >= 256 && this.vocabSize <= 1_048_576 && this.maxSteps >= 1 && this.maxSteps <= 100_000_000,
    );
  }

  dryRun(): void {
    if (!this.valid()) return;
    this.execute(false);
  }

  start(): void {
    if (!this.valid()) return;
    this.execute(true);
  }

  private execute(start: boolean): void {
    this.busy = true;
    this.error = '';
    this.api.resolveResearchRecipe(this.hubUrl, this.recipeRequest()).pipe(
      switchMap(resolved => {
        this.resolved = resolved;
        const request = this.runRequest(resolved);
        return start
          ? this.api.createResearchTraining(this.hubUrl, request, idempotencyKey('research-training'))
          : this.api.dryRunResearchTraining(this.hubUrl, request);
      }),
      finalize(() => { this.busy = false; }),
    ).subscribe({
      next: result => {
        if ('run_id' in result) this.acceptedRunId = result.run_id;
        else this.preflight = result;
      },
      error: error => { this.error = apiErrorMessage(error, 'Research Training wurde vom Hub abgelehnt.'); },
    });
  }

  private recipeRequest(): ResearchRecipeRequest {
    return {
      recipe_id: 'ui-depth-recipe', model_family: this.modelFamily.trim(), architecture: this.architecture.trim(),
      depth: Number(this.depth), context_length: Number(this.contextLength), vocab_size: Number(this.vocabSize),
      max_steps: Number(this.maxSteps), seed: 7, precision: 'float32', world_size: 1, allow_rl: false,
    };
  }

  private runRequest(resolved: ResearchResolvedRecipe): ResearchTrainingRequest {
    const recipe: ResearchTrainingRecipe = {
      schema: resolved.schema,
      recipe_id: resolved.recipe_id,
      recipe_version: resolved.recipe_version,
      model_family: resolved.model_family,
      architecture: resolved.architecture,
      depth: resolved.depth,
      context_length: resolved.context_length,
      vocab_size: resolved.vocab_size,
      max_steps: resolved.max_steps,
      seed: resolved.seed,
      precision: resolved.precision,
      world_size: resolved.world_size,
      allow_rl: resolved.allow_rl,
      resolved_hyperparameters: resolved.resolved_hyperparameters,
    };
    const stages = this.stages();
    return {
      spec: {
        schema: 'ananta.research-training-run.v1', spec_id: `research-${Date.now()}`, mode: 'dry_run',
        dataset_manifest_digest: this.datasetDigest, source_revision_digest: this.sourceRevisionDigest,
        recipe,
        pipeline: {
          schema: 'ananta.research-training-pipeline.v1', pipeline_id: 'ui-safe-pipeline',
          pipeline_version: 'v1', stages, automatic_release: true,
        },
        budget: { gpu_hours: 1, storage_bytes: 10_737_418_240, estimated_cost_microunits: 0 },
      },
    };
  }

  private stages(): ResearchStage[] {
    const definitions: Array<[ResearchStage['kind'], string, string[], string]> = [
      ['tokenizer_train', 'tokenizer', [], 'tokenizer_training'],
      ['tokenizer_eval', 'tokenizer-eval', ['tokenizer'], 'tokenizer_evaluation'],
      ['pretrain', 'pretrain', ['tokenizer-eval'], 'full_weight_training'],
      ['base_eval', 'base-eval', ['pretrain'], 'model_evaluation'],
      ['sft', 'sft', ['base-eval'], 'full_weight_training'],
      ['chat_eval', 'chat-eval', ['sft'], 'model_evaluation'],
      ['inference_benchmark', 'benchmark', ['chat-eval'], 'inference_benchmark'],
      ['export', 'export', ['benchmark'], 'model_export'],
    ];
    return definitions.map(([kind, stage_id, dependencies, required_capability]) => ({
      stage_id, kind, dependencies, required_capability, max_attempts: 2, timeout_seconds: 3600,
    }));
  }
}
