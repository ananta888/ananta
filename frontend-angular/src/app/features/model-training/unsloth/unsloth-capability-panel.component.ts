import { CommonModule } from '@angular/common';
import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  TrainingCapabilities,
  UnslothCapabilityFacet,
  UnslothModality,
  UnslothMutationCommand,
  UnslothMutationOperation,
  UnslothMutationResult,
  UnslothRuntimeHandoffCommandFields,
  UnslothStorageArtifact,
  UnslothStorageCleanupFields,
  UnslothStorageReadModel,
} from '../model-training.models';
import { UnslothHubMutationService } from './unsloth-hub-mutation.service';

const NOT_REPORTED = 'capability_not_reported_by_hub';
const MIN_REASON_LENGTH = 10;
const OPAQUE_RESOURCE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const MODALITIES: readonly UnslothModality[] = ['text', 'vision', 'audio', 'embedding'];
const OPERATIONS: readonly UnslothMutationOperation[] = ['export', 'runtime_handoff', 'mcp', 'cleanup'];

export interface ProjectedUnslothFacet {
  key: string;
  label: string;
  available: boolean;
  reasonCode: string;
  version?: string;
  reported: boolean;
}

export interface UnslothCapabilityProjection {
  core: ProjectedUnslothFacet;
  studio: ProjectedUnslothFacet;
  mcp: ProjectedUnslothFacet;
  releaseProfile: ProjectedUnslothFacet;
  modalities: ProjectedUnslothFacet[];
  operations: Record<UnslothMutationOperation, ProjectedUnslothFacet>;
}

export interface UnslothMutationSummary {
  operation: UnslothMutationOperation;
  resource_id: string;
  reason: string;
  dry_run: true;
  confirmed: false;
  transport: 'hub_only';
  endpoint: string;
  runtimeHandoff?: UnslothRuntimeHandoffCommandFields;
  cleanup?: UnslothStorageCleanupFields;
}

export interface UnslothMutationDraft {
  summary: UnslothMutationSummary | null;
  error: string | null;
}

function projectFacet(
  key: string,
  label: string,
  facet?: UnslothCapabilityFacet,
): ProjectedUnslothFacet {
  if (!facet) {
    return {
      key,
      label,
      available: false,
      reasonCode: NOT_REPORTED,
      reported: false,
    };
  }
  return {
    key,
    label,
    available: facet.available ?? facet.executable ?? false,
    reasonCode: facet.reason_code ?? ((facet.available ?? facet.executable) ? 'available' : 'unavailable'),
    version: facet.version,
    reported: true,
  };
}

export function projectUnslothCapabilities(
  capabilities: TrainingCapabilities | null,
): UnslothCapabilityProjection {
  const unsloth = capabilities?.unsloth;
  const operations = unsloth?.operations;
  const backend = capabilities?.backends?.find((candidate) => {
    const raw = candidate as unknown as Record<string, unknown>;
    return raw['id'] === 'unsloth' || raw['backend'] === 'unsloth';
  });
  const backendFacet = backend
    ? {
        available: Boolean((backend as unknown as Record<string, unknown>)['available']),
        reason_code: (backend as unknown as Record<string, unknown>)['reason_code'] as
          | string
          | undefined,
      }
    : undefined;

  const releaseProfile = projectFacet(
    'release_profile',
    unsloth?.release_profile?.name
      ? `Release: ${unsloth.release_profile.name}`
      : 'Release-Profil',
    unsloth?.release_profile,
  );

  return {
    core: projectFacet(
      'core',
      'Core',
      unsloth?.core ?? (
        unsloth?.status
          ? { available: unsloth.status === 'available', reason_code: unsloth.status }
          : backendFacet
      ),
    ),
    studio: projectFacet('studio', 'Studio', unsloth?.studio ?? operations?.studio),
    mcp: projectFacet('mcp', 'MCP', unsloth?.mcp ?? operations?.mcp),
    releaseProfile,
    modalities: MODALITIES.map((modality) =>
      projectFacet(
        modality,
        modality,
        unsloth?.modalities?.[modality]
          ?? (modality === 'vision' || modality === 'audio' ? operations?.multimodal : undefined),
      ),
    ),
    operations: Object.fromEntries(
      OPERATIONS.map((operation) => [
        operation,
        projectFacet(operation, operation, operations?.[operation]),
      ]),
    ) as Record<UnslothMutationOperation, ProjectedUnslothFacet>,
  };
}

export function buildUnslothMutationSummary(
  operation: UnslothMutationOperation,
  resourceId: string,
  reason: string,
  runtimeHandoff: UnslothRuntimeHandoffCommandFields | null = null,
  cleanup: UnslothStorageCleanupFields | null = null,
): UnslothMutationDraft {
  if (!OPERATIONS.includes(operation)) {
    return { summary: null, error: 'unsupported_operation' };
  }
  const normalizedResourceId = resourceId.trim();
  if (!OPAQUE_RESOURCE_ID.test(normalizedResourceId)) {
    return { summary: null, error: 'resource_id_must_be_opaque' };
  }
  const normalizedReason = reason.trim();
  if (normalizedReason.length < MIN_REASON_LENGTH) {
    return { summary: null, error: 'reason_too_short' };
  }
  if (
    operation === 'runtime_handoff' &&
    (!runtimeHandoff ||
      !OPAQUE_RESOURCE_ID.test(runtimeHandoff.promoted_artifact_id) ||
      !SHA256.test(runtimeHandoff.promoted_artifact_sha256) ||
      !OPAQUE_RESOURCE_ID.test(runtimeHandoff.endpoint_descriptor.endpoint_id) ||
      runtimeHandoff.expected_endpoint_revision < 0 ||
      runtimeHandoff.source_ids.length === 0 ||
      runtimeHandoff.run_ids.length === 0 ||
      runtimeHandoff.source_ids.some((value) => !value.startsWith('SRC_')) ||
      runtimeHandoff.run_ids.some((value) => !value.startsWith('RUN_')))
  ) {
    return { summary: null, error: 'runtime_handoff_contract_incomplete' };
  }
  if (
    operation === 'cleanup' &&
    (!cleanup ||
      cleanup.artifact_ids.length === 0 ||
      cleanup.artifact_ids.length > 500 ||
      new Set(cleanup.artifact_ids).size !== cleanup.artifact_ids.length ||
      cleanup.artifact_ids.some((value) => !OPAQUE_RESOURCE_ID.test(value)) ||
      !Number.isSafeInteger(cleanup.expected_catalog_revision) ||
      cleanup.expected_catalog_revision < 0)
  ) {
    return { summary: null, error: 'storage_cleanup_contract_incomplete' };
  }
  return {
    summary: {
      operation,
      resource_id: normalizedResourceId,
      reason: normalizedReason,
      dry_run: true,
      confirmed: false,
      transport: 'hub_only',
      endpoint: `/api/ml-intern-training/unsloth/mutations/${operation}`,
      runtimeHandoff: operation === 'runtime_handoff' ? runtimeHandoff ?? undefined : undefined,
      cleanup: operation === 'cleanup' ? cleanup ?? undefined : undefined,
    },
    error: null,
  };
}

@Component({
  selector: 'app-unsloth-capability-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="unsloth-panel" aria-labelledby="unsloth-heading">
      <header class="panel-heading">
        <div>
          <p class="eyebrow">Unsloth Capability Matrix</p>
          <h2 id="unsloth-heading">Release-Profil und sichere Übergaben</h2>
        </div>
        <span
          class="core-state"
          [class.available]="projection().core.available"
          [class.unavailable]="!projection().core.available"
        >
          Core {{ projection().core.available ? 'verfügbar' : 'nicht verfügbar' }}
        </span>
      </header>

      <div class="facet-grid">
        @for (facet of primaryFacets(); track facet.key) {
          <article class="facet" [class.available]="facet.available">
            <div class="facet-title">
              <strong>{{ facet.label }}</strong>
              <span>{{ facet.available ? 'bereit' : 'inaktiv' }}</span>
            </div>
            <code>{{ facet.reasonCode }}</code>
            @if (facet.version) {
              <small>Version {{ facet.version }}</small>
            }
          </article>
        }
      </div>

      @if (projection().core.available && optionalProfilesUnavailable()) {
        <p class="core-note">
          Der Core bleibt nutzbar. Nicht verfügbare oder noch nicht vom Hub gemeldete
          Studio-, MCP- und Modality-Profile werden isoliert deaktiviert.
        </p>
      }

      <div class="modalities" aria-label="Unsloth Modalitäten">
        @for (modality of projection().modalities; track modality.key) {
          <span [class.available]="modality.available">
            {{ modality.label }}
            <code>{{ modality.reasonCode }}</code>
          </span>
        }
      </div>

      <div class="handoff">
        <div class="handoff-copy">
          <p class="eyebrow">Closed operations</p>
          <h3>Dry-Run vor jeder Mutation</h3>
          <p>
            Ressourcen werden ausschließlich als opake IDs über den Hub adressiert. Direkte
            Worker-, Studio- oder Dateisystem-Ziele sind nicht zulässig.
          </p>
        </div>

        <div class="operation-form">
          <label>
            Operation
            <select
              [ngModel]="operation()"
              (ngModelChange)="selectOperation($event)"
              aria-label="Unsloth Operation"
            >
              <option value="export">Export</option>
              <option value="runtime_handoff">Runtime-Handoff</option>
              <option value="mcp">MCP-Mutation</option>
              <option value="cleanup">Storage-Cleanup</option>
            </select>
          </label>

          @if (operation() !== 'cleanup') {
            <label>
              Ressourcen-ID
              <input
                type="text"
                autocomplete="off"
                placeholder="adapter_01J..."
                [ngModel]="resourceId()"
                (ngModelChange)="setResourceId($event)"
              />
            </label>
          } @else {
            <div class="cleanup-resource">
              <span>Ressource</span>
              <code>tenant-storage</code>
            </div>
          }

          <label class="reason">
            Begründung
            <textarea
              rows="2"
              [ngModel]="reason()"
              (ngModelChange)="setReason($event)"
              placeholder="Warum ist diese Übergabe erforderlich?"
            ></textarea>
          </label>

          <div class="operation-state">
            <span [class.available]="selectedOperationCapability().available">
              {{ selectedOperationCapability().available ? 'vom Hub freigegeben' : 'gesperrt' }}
            </span>
            <code>{{ selectedOperationCapability().reasonCode }}</code>
          </div>

          @if (draft().error) {
            <p class="validation" role="alert">{{ draft().error }}</p>
          }

          @if (draft().summary; as summary) {
            <dl class="summary">
              <div><dt>Transport</dt><dd>{{ summary.transport }}</dd></div>
              <div><dt>Operation</dt><dd>{{ summary.operation }}</dd></div>
              <div><dt>Ressource</dt><dd>{{ summary.resource_id }}</dd></div>
              <div><dt>Route</dt><dd><code>{{ summary.endpoint }}</code></dd></div>
              @if (summary.runtimeHandoff; as handoff) {
                <div><dt>Export</dt><dd>{{ handoff.promoted_artifact_id }}</dd></div>
                <div><dt>Endpoint</dt><dd>{{ handoff.endpoint_descriptor.endpoint_id }}</dd></div>
                <div><dt>Provider</dt><dd>{{ handoff.provider_descriptor.provider_id }}</dd></div>
                <div><dt>Revision-Fence</dt><dd>{{ handoff.expected_endpoint_revision }}</dd></div>
              }
              @if (summary.cleanup; as cleanup) {
                <div><dt>Artefakte</dt><dd>{{ cleanup.artifact_ids.length }}</dd></div>
                <div><dt>Katalog-Revision</dt><dd>{{ cleanup.expected_catalog_revision }}</dd></div>
              }
            </dl>
          }

          <div class="actions">
            <button type="button" (click)="runDryRun()" [disabled]="!canDryRun()">
              {{ pending() ? 'Hub prüft…' : 'Dry-Run über Hub' }}
            </button>
            <label class="confirmation">
              <input
                type="checkbox"
                [ngModel]="confirmed()"
                (ngModelChange)="confirmed.set($event)"
              />
              Dry-Run-Zusammenfassung geprüft
            </label>
            <button
              class="confirm"
              type="button"
              (click)="submitMutation()"
              [disabled]="!canSubmit()"
            >
              Mutation bestätigen
            </button>
          </div>

          @if (result(); as currentResult) {
            <p class="result" [class.accepted]="currentResult.accepted">
              {{ currentResult.accepted ? 'Vom Hub akzeptiert' : 'Vom Hub abgelehnt' }}
              <code>{{ currentResult.reason_code ?? 'no_reason_code' }}</code>
            </p>
          }
          @if (requestError()) {
            <p class="validation" role="alert">{{ requestError() }}</p>
          }
        </div>
      </div>

      <section class="storage" aria-labelledby="unsloth-storage-heading">
        <div class="storage-heading">
          <div>
            <p class="eyebrow">Tenant-bound storage</p>
            <h3 id="unsloth-storage-heading">Quoten und Cleanup-Kandidaten</h3>
          </div>
          <button type="button" class="secondary" (click)="storageRefresh.emit()" [disabled]="storageLoading()">
            {{ storageLoading() ? 'Storage wird geladen…' : 'Storage aktualisieren' }}
          </button>
        </div>
        @if (storage(); as currentStorage) {
          <div class="storage-metrics">
            <span>
              <strong>{{ bytes(currentStorage.usage.tenant_total_bytes) }}</strong>
              von {{ bytes(currentStorage.usage.quotas.tenant_total_bytes) }}
            </span>
            <span>Revision <strong>{{ currentStorage.usage.catalog_revision }}</strong></span>
            <span>Retention <strong>{{ currentStorage.usage.quotas.retention_seconds }} s</strong></span>
          </div>
          <div class="storage-kinds" role="list" aria-label="Storage-Nutzung nach Artefaktart">
            @for (entry of storageKindUsage(); track entry.kind) {
              <span role="listitem">{{ entry.kind }} · {{ bytes(entry.bytes) }} · {{ entry.artifacts }} Artefakte</span>
            }
          </div>
          @if (currentStorage.items.length) {
            <div class="storage-items">
              @for (artifact of currentStorage.items; track artifact.artifact_id) {
                <label class="storage-item">
                  <input
                    type="checkbox"
                    [checked]="cleanupSelected(artifact.artifact_id)"
                    [disabled]="artifact.referenced || !!artifact.cleanup_task_id"
                    [attr.aria-label]="'Storage-Artefakt ' + artifact.artifact_id + ' für Cleanup auswählen'"
                    (change)="toggleCleanupArtifact(artifact, $any($event.target).checked)"
                  />
                  <span>
                    <strong>{{ artifact.artifact_id }}</strong>
                    <small>
                      {{ artifact.kind }} · {{ bytes(artifact.size_bytes) }} · {{ artifact.state }}
                      @if (artifact.referenced) { · geschützt: {{ artifact.reference_kinds.join(', ') }} }
                    </small>
                  </span>
                </label>
              }
            </div>
          } @else {
            <p class="core-note">Der Hub meldet aktuell keine Storage-Artefakte.</p>
          }
        } @else {
          <p class="core-note">Noch kein pfadfreies Storage-Readmodel vom Hub geladen.</p>
        }
      </section>
    </section>
  `,
  styles: [
    `
      .unsloth-panel {
        margin: 1rem 0 1.5rem;
        padding: clamp(1rem, 2vw, 1.5rem);
        border: 1px solid var(--border);
        border-radius: 1.1rem;
        background:
          radial-gradient(circle at 100% 0%, color-mix(in srgb, var(--accent) 12%, transparent), transparent 34%),
          var(--surface);
      }
      .panel-heading,
      .facet-title,
      .actions,
      .operation-state,
      .result {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
      }
      h2,
      h3,
      p {
        margin: 0;
      }
      h2 {
        font-size: clamp(1.15rem, 2vw, 1.45rem);
      }
      h3 {
        font-size: 1.05rem;
      }
      .eyebrow {
        margin-bottom: 0.25rem;
        color: var(--text-muted);
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
      }
      .core-state,
      .facet-title span,
      .operation-state span {
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 0.25rem 0.55rem;
        font-size: 0.72rem;
        white-space: nowrap;
      }
      .available {
        border-color: color-mix(in srgb, var(--accent) 55%, var(--border)) !important;
        color: var(--accent);
      }
      .unavailable,
      .validation {
        color: #9f3029;
      }
      .facet-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.65rem;
        margin-top: 1rem;
      }
      .facet {
        display: grid;
        gap: 0.55rem;
        min-width: 0;
        padding: 0.75rem;
        border: 1px solid var(--border);
        border-radius: 0.8rem;
        background: color-mix(in srgb, var(--surface) 88%, var(--border));
      }
      code {
        overflow-wrap: anywhere;
        color: var(--text-muted);
        font-size: 0.72rem;
      }
      small,
      .handoff-copy p,
      .core-note {
        color: var(--text-muted);
      }
      .core-note {
        margin-top: 0.8rem;
        padding-left: 0.75rem;
        border-left: 3px solid var(--accent);
        font-size: 0.82rem;
      }
      .modalities {
        display: flex;
        flex-wrap: wrap;
        gap: 0.45rem;
        margin-top: 0.8rem;
      }
      .modalities > span {
        display: inline-flex;
        gap: 0.4rem;
        padding: 0.32rem 0.55rem;
        border: 1px solid var(--border);
        border-radius: 999px;
        font-size: 0.75rem;
      }
      .handoff {
        display: grid;
        grid-template-columns: minmax(12rem, 0.7fr) minmax(20rem, 1.3fr);
        gap: 1rem;
        margin-top: 1.15rem;
        padding-top: 1.15rem;
        border-top: 1px solid var(--border);
      }
      .handoff-copy {
        display: grid;
        align-content: start;
        gap: 0.4rem;
      }
      .handoff-copy p:last-child {
        font-size: 0.82rem;
        line-height: 1.5;
      }
      .operation-form {
        display: grid;
        grid-template-columns: minmax(0, 0.7fr) minmax(0, 1.3fr);
        gap: 0.65rem;
      }
      label {
        display: grid;
        gap: 0.3rem;
        color: var(--text-muted);
        font-size: 0.74rem;
        font-weight: 600;
      }
      input,
      select,
      textarea {
        width: 100%;
        box-sizing: border-box;
        border: 1px solid var(--border);
        border-radius: 0.6rem;
        padding: 0.6rem 0.7rem;
        background: var(--surface);
        color: inherit;
        font: inherit;
      }
      .reason,
      .cleanup-resource,
      .operation-state,
      .validation,
      .summary,
      .actions,
      .result {
        grid-column: 1 / -1;
      }
      .cleanup-resource {
        display: grid;
        gap: 0.3rem;
        align-content: center;
        padding: 0.45rem 0;
        color: var(--text-muted);
        font-size: 0.74rem;
        font-weight: 600;
      }
      .operation-state {
        justify-content: flex-start;
      }
      .validation {
        margin: 0;
        font-size: 0.76rem;
      }
      .summary {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.45rem;
        margin: 0;
        padding: 0.7rem;
        border: 1px dashed var(--border);
        border-radius: 0.7rem;
      }
      .summary div {
        min-width: 0;
      }
      dt {
        color: var(--text-muted);
        font-size: 0.68rem;
        text-transform: uppercase;
      }
      dd {
        margin: 0.12rem 0 0;
        font-size: 0.8rem;
        overflow-wrap: anywhere;
      }
      button {
        border: 1px solid var(--accent);
        border-radius: 0.6rem;
        padding: 0.58rem 0.8rem;
        background: transparent;
        color: var(--accent);
        font-weight: 700;
        cursor: pointer;
      }
      button.confirm {
        background: var(--accent);
        color: var(--surface);
      }
      button:disabled {
        opacity: 0.45;
        cursor: not-allowed;
      }
      .confirmation {
        display: flex;
        grid-template-columns: auto 1fr;
        align-items: center;
      }
      .confirmation input {
        width: auto;
      }
      .result {
        justify-content: flex-start;
        padding: 0.6rem 0.7rem;
        border: 1px solid var(--border);
        border-radius: 0.6rem;
        font-size: 0.78rem;
      }
      .result.accepted {
        border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
      }
      .storage {
        display: grid;
        gap: 0.75rem;
        margin-top: 1rem;
        padding-top: 1rem;
        border-top: 1px solid var(--border);
      }
      .storage-heading,
      .storage-metrics,
      .storage-kinds {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.65rem;
        flex-wrap: wrap;
      }
      .storage-metrics > span,
      .storage-kinds > span {
        padding: 0.4rem 0.6rem;
        border: 1px solid var(--border);
        border-radius: 999px;
        font-size: 0.75rem;
      }
      .storage-items {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.5rem;
      }
      .storage-item {
        display: flex;
        grid-template-columns: none;
        align-items: flex-start;
        gap: 0.55rem;
        padding: 0.65rem;
        border: 1px solid var(--border);
        border-radius: 0.7rem;
      }
      .storage-item input {
        width: auto;
        margin-top: 0.15rem;
      }
      .storage-item span {
        display: grid;
        min-width: 0;
        gap: 0.15rem;
      }
      @media (max-width: 900px) {
        .facet-grid {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .handoff {
          grid-template-columns: 1fr;
        }
      }
      @media (max-width: 600px) {
        .panel-heading,
        .actions {
          align-items: stretch;
          flex-direction: column;
        }
        .facet-grid,
        .operation-form,
        .summary {
          grid-template-columns: 1fr;
        }
        .storage-items {
          grid-template-columns: 1fr;
        }
      }
    `,
  ],
})
export class UnslothCapabilityPanelComponent {
  readonly capabilities = input<TrainingCapabilities | null>(null);
  readonly hubUrl = input('');
  readonly runtimeHandoff = input<UnslothRuntimeHandoffCommandFields | null>(null);
  readonly storage = input<UnslothStorageReadModel | null>(null);
  readonly storageLoading = input(false);
  readonly storageRefresh = output<void>();

  private readonly mutationService = inject(UnslothHubMutationService);

  readonly operation = signal<UnslothMutationOperation>('export');
  readonly resourceId = signal('');
  readonly reason = signal('');
  readonly confirmed = signal(false);
  readonly pending = signal(false);
  readonly result = signal<UnslothMutationResult | null>(null);
  readonly requestError = signal<string | null>(null);
  private readonly cleanupArtifactIds = signal<string[]>([]);
  private readonly dryRunFingerprint = signal<string | null>(null);
  private readonly confirmationId = signal<string | null>(null);

  readonly projection = computed(() => projectUnslothCapabilities(this.capabilities()));
  readonly primaryFacets = computed(() => {
    const current = this.projection();
    return [current.core, current.studio, current.mcp, current.releaseProfile];
  });
  readonly optionalProfilesUnavailable = computed(() => {
    const current = this.projection();
    return (
      !current.studio.available ||
      !current.mcp.available ||
      current.modalities.some((modality) => !modality.available)
    );
  });
  readonly selectedOperationCapability = computed(
    () => this.projection().operations[this.operation()],
  );
  readonly draft = computed(() =>
    buildUnslothMutationSummary(
      this.operation(),
      this.effectiveResourceId(),
      this.reason(),
      this.runtimeHandoff(),
      this.cleanupFields(),
    ),
  );
  readonly canDryRun = computed(
    () =>
      Boolean(this.draft().summary) &&
      this.selectedOperationCapability().available &&
      Boolean(this.hubUrl()) &&
      !this.pending(),
  );
  readonly canSubmit = computed(
    () =>
      this.canDryRun() &&
      this.confirmed() &&
      this.dryRunFingerprint() === this.currentFingerprint() &&
      Boolean(this.confirmationId()),
  );

  selectOperation(value: string): void {
    if (value === 'export' || value === 'runtime_handoff' || value === 'mcp' || value === 'cleanup') {
      this.operation.set(value);
      this.resetApproval();
    }
  }

  setResourceId(value: string): void {
    this.resourceId.set(value);
    this.resetApproval();
  }

  setReason(value: string): void {
    this.reason.set(value);
    this.resetApproval();
  }

  cleanupSelected(artifactId: string): boolean {
    return this.cleanupArtifactIds().includes(artifactId);
  }

  toggleCleanupArtifact(artifact: UnslothStorageArtifact, selected: boolean): void {
    if (artifact.referenced || artifact.cleanup_task_id) return;
    this.cleanupArtifactIds.update((current) => {
      if (!selected) return current.filter((value) => value !== artifact.artifact_id);
      const maxItems = this.storage()?.usage.quotas.max_cleanup_items ?? 0;
      if (maxItems > 0 && current.length >= maxItems) return current;
      return Array.from(new Set([...current, artifact.artifact_id]));
    });
    this.resetApproval();
  }

  storageKindUsage(): Array<{ kind: string; bytes: number; artifacts: number }> {
    return Object.entries(this.storage()?.usage.usage || {})
      .map(([kind, usage]) => ({ kind, ...usage }))
      .sort((left, right) => left.kind.localeCompare(right.kind));
  }

  bytes(value: number): string {
    const bytes = Math.max(0, Number(value || 0));
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }

  runDryRun(): void {
    const summary = this.draft().summary;
    if (!summary || !this.canDryRun()) {
      return;
    }
    this.send(
      {
        operation: summary.operation,
        resource_id: summary.resource_id,
        reason: summary.reason,
        dry_run: true,
        confirmed: false,
        ...this.runtimeFields(summary),
        ...this.cleanupCommandFields(summary),
      },
      true,
    );
  }

  submitMutation(): void {
    const summary = this.draft().summary;
    if (!summary || !this.canSubmit()) {
      return;
    }
    this.send(
      {
        operation: summary.operation,
        resource_id: summary.resource_id,
        reason: summary.reason,
        dry_run: false,
        confirmed: true,
        confirmation_id: this.confirmationId() ?? undefined,
        ...this.runtimeFields(summary),
        ...this.cleanupCommandFields(summary),
      },
      false,
    );
  }

  private send(command: UnslothMutationCommand, isDryRun: boolean): void {
    const fingerprint = this.currentFingerprint();
    this.pending.set(true);
    this.result.set(null);
    this.requestError.set(null);
    this.mutationService
      .submit(this.hubUrl(), command, this.newIdempotencyKey())
      .subscribe({
        next: (result) => {
          this.pending.set(false);
          this.result.set(result);
          if (isDryRun && result.accepted && result.confirmation_id) {
            this.dryRunFingerprint.set(fingerprint);
            this.confirmationId.set(result.confirmation_id);
          } else if (isDryRun && result.accepted) {
            this.requestError.set('unsloth_confirmation_missing');
          } else if (!isDryRun) {
            if (command.operation === 'cleanup' && result.accepted) {
              this.storageRefresh.emit();
            }
            this.confirmed.set(false);
            this.dryRunFingerprint.set(null);
            this.confirmationId.set(null);
          }
        },
        error: (error: unknown) => {
          this.pending.set(false);
          this.dryRunFingerprint.set(null);
          this.confirmationId.set(null);
          this.requestError.set(error instanceof Error ? error.message : 'unsloth_hub_request_failed');
        },
      });
  }

  private currentFingerprint(): string {
    return JSON.stringify([
      this.operation(),
      this.effectiveResourceId().trim(),
      this.reason().trim(),
      this.operation() === 'runtime_handoff' ? this.runtimeHandoff() : null,
      this.operation() === 'cleanup' ? this.cleanupFields() : null,
    ]);
  }

  private resetApproval(): void {
    this.confirmed.set(false);
    this.dryRunFingerprint.set(null);
    this.confirmationId.set(null);
    this.result.set(null);
    this.requestError.set(null);
  }

  private runtimeFields(
    summary: UnslothMutationSummary,
  ): Partial<UnslothMutationCommand> {
    return summary.runtimeHandoff ? { ...summary.runtimeHandoff } : {};
  }

  private cleanupCommandFields(
    summary: UnslothMutationSummary,
  ): Partial<UnslothMutationCommand> {
    return summary.cleanup ? { ...summary.cleanup } : {};
  }

  private cleanupFields(): UnslothStorageCleanupFields | null {
    if (this.operation() !== 'cleanup') return null;
    const currentStorage = this.storage();
    const available = new Set(currentStorage?.items
      .filter((item) => !item.referenced && !item.cleanup_task_id)
      .map((item) => item.artifact_id) || []);
    const artifactIds = this.cleanupArtifactIds().filter((artifactId) => available.has(artifactId));
    const maxItems = currentStorage?.usage.quotas.max_cleanup_items ?? 0;
    if (maxItems > 0 && artifactIds.length > maxItems) return null;
    return {
      artifact_ids: artifactIds,
      expected_catalog_revision: currentStorage?.usage.catalog_revision ?? -1,
    };
  }

  private effectiveResourceId(): string {
    return this.operation() === 'cleanup' ? 'tenant-storage' : this.resourceId();
  }

  private newIdempotencyKey(): string {
    return globalThis.crypto?.randomUUID?.() ?? `unsloth-${Date.now().toString(36)}`;
  }
}
