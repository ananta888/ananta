import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

import { VpRuntimeOverlay, VpStep } from './visual-process-api.service';
import {
  VpDatasetBuildRuntimeView,
  VpTrainingRuntimeView,
  extractVpDatasetBuildRuntime,
  extractVpTrainingRuntime,
  stringifyVpRuntimeResult,
} from './vp-model-training-contract';

interface LegacyTrainingField {
  key: string;
  value: unknown;
}

const LEGACY_TRAINING_FIELDS = [
  'dataset_path', 'datasetPath', 'dataset_root', 'datasetRoot',
  'source_paths', 'sourcePaths', 'output_path', 'outputPath',
  'artifact_root', 'artifactRoot', 'gpu_profile', 'output_dir', 'outputDir',
  'enabled', 'training_config', 'trainingConfig',
];

const LEGACY_DATASET_BUILD_FIELDS = [
  'dataset_path', 'datasetPath', 'dataset_root', 'datasetRoot',
  'source_paths', 'sourcePaths', 'output_path', 'outputPath',
];

/** Focused extension for non-definition training UX: links, runtime truth and legacy quarantine. */
@Component({
  selector: 'app-vp-training-node-extension',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrls: ['./visual-process-editor.component.scss'],
  template: `
    @if (isDatasetBuild()) {
      <section class="vpe-meta-section" data-testid="vp-dataset-build-extension">
        <div class="vpe-info-note">Dieser Hub-Step übernimmt ausschließlich eine Dataset-ID oder bounded Records aus einem Upstream-Artefakt. Erstellung, Split, Secret-Scan und Validierung laufen über den Dataset-Katalog.</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <a class="vpe-btn-xs" href="/model-training?tab=datasets">Dataset-Control-Center</a>
          @if (datasetId()) { <a class="vpe-btn-xs" [href]="datasetUrl()">Gewähltes Dataset öffnen</a> }
        </div>
        @if (legacyDatasetBuildFields().length) {
          <div class="vpe-warn-note" data-testid="vp-dataset-build-legacy">
            <strong>Deprecated und nur lesbar:</strong> Legacy-Pfade werden nicht als neuer Vertrag gespeichert oder direkt ausgeführt. Eine Migration erfolgt ausschließlich über den Hub-Quarantäne-Adapter.
            @for (field of legacyDatasetBuildFields(); track field.key) {
              <div><code>{{ field.key }}</code>: <code>{{ formatValue(field.value) }}</code></div>
            }
          </div>
        }
        @if (datasetBuildRuntime(); as runtime) {
          <div class="vpe-rt-panel" data-testid="vp-dataset-build-runtime">
            <div class="vpe-panel-title">Katalogisiertes Dataset</div>
            <div><span class="vpe-rt-key">Dataset-ID:</span> {{ runtime.datasetId }}</div>
            <div><span class="vpe-rt-key">Status:</span> {{ runtime.status }} / {{ runtime.validationStatus || 'unbekannt' }}</div>
            <div><span class="vpe-rt-key">Train / Validation:</span> {{ runtime.trainRecordCount }} / {{ runtime.validationRecordCount }}</div>
            @if (runtime.sourceMode) { <div><span class="vpe-rt-key">Quelle:</span> {{ runtime.sourceMode }}</div> }
            <a class="vpe-btn-xs" [href]="runtime.datasetUrl">Dataset öffnen</a>
          </div>
        }
      </section>
    }
    @if (isTraining()) {
      <section class="vpe-meta-section" data-testid="vp-training-extension">
        <div class="vpe-warn-note">LoRA-Training wird als Hub-eigener Job ausgeführt und schreibt Adapter-Artefakte. Dry-run ist der sichere Standard; Live-Läufe benötigen die bestehende Hub-Governance.</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <a class="vpe-btn-xs" href="/model-training">Training-Control-Center</a>
          <a class="vpe-btn-xs" [href]="datasetUrl()">Dataset-Control-Center</a>
        </div>
        @if (legacyTrainingFields().length) {
          <div class="vpe-warn-note" data-testid="vp-training-legacy">
            <strong>Deprecated:</strong> Diese Legacy-Felder bleiben zur Migration lesbar, werden hier aber nicht bearbeitet. Verwende autorisierte Dataset-, Trainingsprofil- und Basismodell-IDs.
            @for (field of legacyTrainingFields(); track field.key) {
              <div><code>{{ field.key }}</code>: <code>{{ formatValue(field.value) }}</code></div>
            }
          </div>
        }
        @if (trainingRuntime(); as runtime) {
          <div class="vpe-rt-panel" data-testid="vp-training-runtime">
            <div class="vpe-panel-title">Lokaler Trainingsjob</div>
            <div><span class="vpe-rt-key">Job-ID:</span> {{ runtime.jobId }}</div>
            <div><span class="vpe-rt-key">Status / Phase:</span> {{ runtime.status }} / {{ runtime.phase }}</div>
            <div><span class="vpe-rt-key">Terminal:</span> {{ runtime.terminal ? 'ja' : 'nein' }}</div>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              <a class="vpe-btn-xs" [href]="runtime.jobUrl">Job im Control Center</a>
              @if (runtime.datasetUrl) { <a class="vpe-btn-xs" [href]="runtime.datasetUrl">Dataset anzeigen</a> }
            </div>
            @if (runtime.terminal) { <pre class="vpe-code">{{ formatValue(runtime.terminalResult) }}</pre> }
          </div>
        }
      </section>
    }
  `,
})
export class VpTrainingNodeExtensionComponent {
  @Input({ required: true }) step!: VpStep;
  @Input() runtimeOverlay: VpRuntimeOverlay | null = null;

  isDatasetBuild(): boolean { return this.step.kind === 'ml_intern_build_lora_dataset'; }
  isTraining(): boolean { return this.step.kind === 'ml_intern_train_lora'; }

  datasetId(): string {
    return String(this.step.metadata?.['dataset_id'] ?? this.step.metadata?.['datasetId'] ?? '').trim();
  }

  datasetUrl(): string {
    const id = this.datasetId();
    return id ? `/model-training?tab=datasets&dataset_id=${encodeURIComponent(id)}` : '/model-training?tab=datasets';
  }

  legacyTrainingFields(): LegacyTrainingField[] { return this.legacyFields(LEGACY_TRAINING_FIELDS); }
  legacyDatasetBuildFields(): LegacyTrainingField[] { return this.legacyFields(LEGACY_DATASET_BUILD_FIELDS); }

  trainingRuntime(): VpTrainingRuntimeView | null {
    const runtime = this.runtimeOverlay?.steps?.[this.step.id];
    return runtime?.training ?? extractVpTrainingRuntime(runtime);
  }

  datasetBuildRuntime(): VpDatasetBuildRuntimeView | null {
    const runtime = this.runtimeOverlay?.steps?.[this.step.id];
    return runtime?.datasetBuild ?? extractVpDatasetBuildRuntime(runtime);
  }

  formatValue(value: unknown): string { return stringifyVpRuntimeResult(this.redact(value)); }

  private legacyFields(keys: readonly string[]): LegacyTrainingField[] {
    const metadata = this.step.metadata ?? {};
    return keys.filter(key => Object.prototype.hasOwnProperty.call(metadata, key))
      .map(key => ({ key, value: metadata[key] }));
  }

  private redact(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(item => this.redact(item));
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => {
      const normalized = key.toLocaleLowerCase('en-US').replaceAll('-', '_');
      const secret = ['api_key', 'apikey', 'access_token', 'refresh_token', 'password', 'client_secret', 'private_key'].includes(normalized)
        && !normalized.endsWith('_secret_ref');
      return [key, secret ? '[REDACTED]' : this.redact(item)];
    }));
  }
}
