import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { finalize } from 'rxjs';
import {
  ContextAccessPolicy,
  ContextAccessRule,
  ContextPolicyMatrixRow,
  ContextPolicyRecord,
  ContextPolicyValidationResult,
} from '../../models/context-access-policy.model';
import {
  ContextPolicyPreview,
  SourceControlAccessDecision,
} from '../../models/source-control-v1-api.model';
import { ContextAccessPolicyApiService } from '../../services/context-access-policy-api.service';
import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';

export interface ContextPolicyUiError {
  state: 'offline' | 'unauthorized' | 'forbidden' | 'not-found' | 'conflict' | 'unprocessable' | 'rate-limited' | 'server-error' | 'error';
  message: string;
  reasonCode?: string;
  conflict: boolean;
}

const MAX_POLICY_RECORDS = 200;
const MAX_MATRIX_ROWS = 500;

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function serverStrings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item).trim()).filter(Boolean)
    : [];
}

function constraintText(allowed: unknown, denied: unknown): string {
  const allowValues = serverStrings(allowed);
  const denyValues = serverStrings(denied);
  const parts: string[] = [];
  if (allowValues.length) parts.push(`allowed=[${allowValues.join(', ')}]`);
  if (denyValues.length) parts.push(`denied=[${denyValues.join(', ')}]`);
  return parts.join(' · ') || 'Nicht geliefert';
}

function flagText(value: unknown): string {
  if (value === true) return 'true';
  if (value === false) return 'false';
  return 'nicht gesetzt';
}

function sourceText(rule: Record<string, unknown>): string {
  const parts: string[] = [];
  const pattern = String(rule['source_match'] || '').trim();
  const types = serverStrings(rule['source_types']);
  if (pattern) parts.push(`match=${pattern}`);
  if (types.length) parts.push(`types=[${types.join(', ')}]`);
  return parts.join(' · ') || 'Nicht geliefert';
}

function matrixRow(record: ContextPolicyRecord, rule: ContextAccessRule): ContextPolicyMatrixRow {
  const raw = recordOf(rule);
  const reasonTags = serverStrings(raw['reason_tags']);
  return {
    policyId: record.policy_id,
    version: record.version,
    ruleId: String(raw['id'] || '').trim() || 'Nicht geliefert',
    source: sourceText(raw),
    sensitivity: String(raw['sensitivity'] || '').trim() || 'Nicht geliefert',
    workerKinds: constraintText(raw['allowed_worker_kinds'], raw['denied_worker_kinds']),
    runtimeKinds: constraintText(raw['allowed_runtime_kinds'], raw['denied_runtime_kinds']),
    providerLocations: constraintText(raw['allowed_provider_locations'], raw['denied_provider_locations']),
    modelScopes: constraintText(raw['allowed_model_scopes'], raw['denied_model_scopes']),
    operations: [
      `read_allowed=${flagText(raw['read_allowed'])}`,
      `write_allowed=${flagText(raw['write_allowed'])}`,
      `send_allowed=${flagText(raw['send_allowed'])}`,
    ].join(' · '),
    transformations: [
      `redaction_required=${flagText(raw['redaction_required'])}`,
      `summarization_allowed=${flagText(raw['summarization_allowed'])}`,
      `approval_required=${flagText(raw['approval_required'])}`,
    ].join(' · '),
    reasonData: reasonTags.length ? `reason_tags=[${reasonTags.join(', ')}]` : 'Kein Reason-Code geliefert',
  };
}

export function contextPolicyUiError(error: unknown): ContextPolicyUiError {
  const outer = recordOf(error);
  const status = Number(outer['status'] || 0);
  let payload = recordOf(outer['error']);
  for (let depth = 0; depth < 3; depth += 1) {
    const next = recordOf(payload['data']);
    if (!Object.keys(next).length) break;
    payload = next;
  }
  const reasonCode = String(
    payload['reason_code'] || outer['reasonCode'] || '',
  ).trim() || undefined;
  const serverMessage = String(payload['message'] || outer['message'] || '').trim();
  if (status === 0) return { state: 'offline', message: 'Der Hub ist nicht erreichbar.', reasonCode, conflict: false };
  if (status === 401) return { state: 'unauthorized', message: 'Der Hub verlangt eine gültige Anmeldung.', reasonCode, conflict: false };
  if (status === 403) return { state: 'forbidden', message: 'Der Hub hat die Management-Berechtigung nicht bestätigt.', reasonCode, conflict: false };
  if (status === 404) return { state: 'not-found', message: 'Die Policy oder Route wurde nicht gefunden.', reasonCode, conflict: false };
  if (status === 409) return {
    state: 'conflict',
    message: serverMessage || 'Die Policy-Version hat sich serverseitig geändert. Sicher neu laden.',
    reasonCode,
    conflict: true,
  };
  if (status === 422) return {
    state: 'unprocessable',
    message: 'Der 2xx-Policy-Snapshot verletzt den erwarteten Laufzeitvertrag und wird nicht angezeigt.',
    reasonCode,
    conflict: false,
  };
  if (status === 429) return { state: 'rate-limited', message: 'Der Hub begrenzt die Anfrage. Bitte später erneut versuchen.', reasonCode, conflict: false };
  if (status >= 500) return { state: 'server-error', message: 'Der Hub konnte die Policy-Anfrage nicht verarbeiten.', reasonCode, conflict: false };
  return { state: 'error', message: serverMessage || 'Die Policy-Anfrage ist fehlgeschlagen.', reasonCode, conflict: false };
}

@Injectable({ providedIn: 'root' })
export class ContextAccessPolicyFacade {
  private readonly api = inject(ContextAccessPolicyApiService);
  private readonly sourceControlApi = inject(SourceControlV1ApiClient);
  private readonly destroyRef = inject(DestroyRef);

  readonly projectId = signal<string | null>(null);
  readonly policies = signal<readonly ContextPolicyRecord[]>([]);
  readonly selectedPolicy = signal<ContextPolicyRecord | null>(null);
  readonly validation = signal<ContextPolicyValidationResult | null>(null);
  readonly preview = signal<ContextPolicyPreview | null>(null);
  readonly effectiveMatrix = signal<readonly SourceControlAccessDecision[]>([]);
  readonly listLoading = signal(false);
  readonly detailLoading = signal(false);
  readonly validationLoading = signal(false);
  readonly mutationLoading = signal(false);
  readonly matrixLoading = signal(false);
  readonly listConfirmed = signal(false);
  readonly detailConfirmed = signal(false);
  readonly validationConfirmed = signal(false);
  readonly recordsTruncated = signal(false);
  readonly listError = signal<ContextPolicyUiError | null>(null);
  readonly detailError = signal<ContextPolicyUiError | null>(null);
  readonly validationError = signal<ContextPolicyUiError | null>(null);
  readonly matrixError = signal<ContextPolicyUiError | null>(null);
  readonly managementAuthorized = computed(() => this.listConfirmed());
  private readonly matrixProjection = computed<{
    rows: readonly ContextPolicyMatrixRow[];
    truncated: boolean;
  }>(() => {
    const rows: ContextPolicyMatrixRow[] = [];
    let truncated = false;
    for (const policy of this.policies()) {
      for (const rule of policy.policy.rules || []) {
        if (rows.length >= MAX_MATRIX_ROWS) {
          truncated = true;
          break;
        }
        rows.push(matrixRow(policy, rule));
      }
      if (truncated) break;
    }
    return { rows, truncated };
  });
  readonly matrixRows = computed<readonly ContextPolicyMatrixRow[]>(() => this.matrixProjection().rows);
  readonly matrixTruncated = computed(() => this.matrixProjection().truncated);

  initialize(projectId: string | null): void {
    const normalizedId = String(projectId || '').trim();
    this.reset();
    if (!normalizedId) {
      this.listError.set({
        state: 'not-found',
        message: 'Kein autoritativer projectId-Routekontext vorhanden.',
        conflict: false,
      });
      return;
    }
    this.projectId.set(normalizedId);
    this.reload();
  }

  reload(): void {
    const projectId = this.projectId();
    if (!projectId || this.listLoading()) return;
    this.listLoading.set(true);
    this.listError.set(null);
    this.listConfirmed.set(false);
    this.api.listPolicies('', projectId).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.listLoading.set(false)),
    ).subscribe({
      next: (records) => {
        this.recordsTruncated.set(records.length > MAX_POLICY_RECORDS);
        this.policies.set(records.slice(0, MAX_POLICY_RECORDS));
        this.listConfirmed.set(true);
      },
      error: (error) => {
        this.policies.set([]);
        this.selectedPolicy.set(null);
        this.listError.set(contextPolicyUiError(error));
      },
    });
  }

  loadLatest(policyId: string): void {
    const serverRecord = this.policies().find((record) => record.policy_id === policyId);
    if (!serverRecord || !this.managementAuthorized() || this.detailLoading()) return;
    this.detailLoading.set(true);
    this.detailError.set(null);
    this.validation.set(null);
    this.api.getLatestPolicy('', serverRecord.policy_id).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.detailLoading.set(false)),
    ).subscribe({
      next: (record) => {
        this.selectedPolicy.set(record);
        this.detailConfirmed.set(true);
      },
      error: (error) => {
        this.detailConfirmed.set(false);
        this.detailError.set(contextPolicyUiError(error));
      },
    });
  }

  validateSelected(): void {
    const selected = this.selectedPolicy();
    if (!selected || !this.managementAuthorized() || this.validationLoading()) return;
    this.validationLoading.set(true);
    this.validation.set(null);
    this.validationError.set(null);
    this.sourceControlApi.lintContextPolicy(
      selected.policy_id,
      selected.version,
    ).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.validationLoading.set(false)),
    ).subscribe({
      next: ({ diagnostics }) => {
        const errors = diagnostics
          .filter((item) => item.severity === 'error')
          .map((item) => item.reason_code);
        this.validation.set({
          status: errors.length === 0 ? 'success' : 'error',
          valid: errors.length === 0,
          errors,
        });
        this.validationConfirmed.set(true);
      },
      error: (error) => {
        this.validationConfirmed.set(false);
        this.validationError.set(contextPolicyUiError(error));
      },
    });
  }

  loadEffectiveMatrix(
    operation: string,
    transformation: string,
    purpose: string,
  ): void {
    if (!this.managementAuthorized() || this.matrixLoading()) return;
    this.matrixLoading.set(true);
    this.matrixError.set(null);
    this.effectiveMatrix.set([]);
    this.sourceControlApi.loadAccessMatrix({
      operation,
      transformation,
      purpose,
      source_limit: 25,
      destination_limit: 25,
    }).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.matrixLoading.set(false)),
    ).subscribe({
      next: (matrix) => this.effectiveMatrix.set(matrix.items),
      error: (error) => this.matrixError.set(contextPolicyUiError(error)),
    });
  }

  previewSelected(
    sourceRevisionId: string,
    destinationId: string,
    operation: string,
    transformation: string,
  ): void {
    const selected = this.selectedPolicy();
    if (!selected || !this.managementAuthorized() || this.validationLoading()) return;
    this.validationLoading.set(true);
    this.preview.set(null);
    this.effectiveMatrix.set([]);
    this.validationError.set(null);
    this.matrixError.set(null);
    this.sourceControlApi.previewContextPolicy(selected.policy_id, {
      version: selected.version,
      source_revision_id: sourceRevisionId,
      destination_id: destinationId,
      operation,
      transformation,
    }).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.validationLoading.set(false)),
    ).subscribe({
      next: (preview) => this.preview.set(preview),
      error: (error) => this.validationError.set(contextPolicyUiError(error)),
    });
  }

  createDraft(
    policy: ContextAccessPolicy,
    expectedLatestVersion: number | null,
  ): void {
    if (!this.managementAuthorized() || this.mutationLoading()) return;
    this.mutationLoading.set(true);
    this.detailError.set(null);
    this.sourceControlApi.createContextPolicyDraft(
      policy.policy_id,
      {
        document: {
          schema: 'ananta.context-access-policy.v1',
          policy_id: policy.policy_id,
          scope: policy.scope,
          defaults: policy.defaults,
          rules: policy.rules,
          precedence: policy.precedence,
        },
        expected_latest_version: expectedLatestVersion,
      },
      this.idempotencyKey(),
    ).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.mutationLoading.set(false)),
    ).subscribe({
      next: () => this.reload(),
      error: (error) => this.detailError.set(contextPolicyUiError(error)),
    });
  }

  activateSelected(): void {
    this.transitionSelected('activate');
  }

  revokeSelected(): void {
    this.transitionSelected('revoke');
  }

  rollbackSelected(targetVersion: number): void {
    const selected = this.selectedPolicy();
    const etag = this.selectedEtag();
    if (
      !selected ||
      !etag ||
      !this.managementAuthorized() ||
      this.mutationLoading()
    ) return;
    this.mutationLoading.set(true);
    this.detailError.set(null);
    this.sourceControlApi.rollbackContextPolicy(
      selected.policy_id,
      {
        target_version: targetVersion,
        expected_latest_version: selected.version,
      },
      { etag, idempotencyKey: this.idempotencyKey() },
    ).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.mutationLoading.set(false)),
    ).subscribe({
      next: () => this.reload(),
      error: (error) => this.detailError.set(contextPolicyUiError(error)),
    });
  }

  safeReloadAfterConflict(): void {
    this.selectedPolicy.set(null);
    this.validation.set(null);
    this.preview.set(null);
    this.detailError.set(null);
    this.validationError.set(null);
    this.reload();
  }

  private reset(): void {
    this.projectId.set(null);
    this.policies.set([]);
    this.selectedPolicy.set(null);
    this.validation.set(null);
    this.listConfirmed.set(false);
    this.detailConfirmed.set(false);
    this.validationConfirmed.set(false);
    this.recordsTruncated.set(false);
    this.listError.set(null);
    this.detailError.set(null);
    this.validationError.set(null);
  }

  private transitionSelected(operation: 'activate' | 'revoke'): void {
    const selected = this.selectedPolicy();
    const etag = this.selectedEtag();
    if (
      !selected ||
      !etag ||
      !this.managementAuthorized() ||
      this.mutationLoading()
    ) return;
    this.mutationLoading.set(true);
    this.detailError.set(null);
    const request = operation === 'activate'
      ? this.sourceControlApi.activateContextPolicy(
          selected.policy_id,
          selected.version,
          { etag, idempotencyKey: this.idempotencyKey() },
        )
      : this.sourceControlApi.revokeContextPolicy(
          selected.policy_id,
          selected.version,
          { etag, idempotencyKey: this.idempotencyKey() },
        );
    request.pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.mutationLoading.set(false)),
    ).subscribe({
      next: () => this.reload(),
      error: (error) => this.detailError.set(contextPolicyUiError(error)),
    });
  }

  private selectedEtag(): string {
    const raw = this.selectedPolicy()?.raw;
    return String(raw?.['etag'] || '').trim();
  }

  private idempotencyKey(): string {
    return `ui:${crypto.randomUUID()}`;
  }
}
