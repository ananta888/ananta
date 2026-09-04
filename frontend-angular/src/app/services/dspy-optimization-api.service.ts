import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { normalizeHubOrigin } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';

export interface DspyOptimizationCapability {
  readonly state: 'disabled' | 'unavailable' | 'degraded' | 'available';
  readonly reasonCode: string;
  readonly mode: string;
  readonly installedVersion: string | null;
  readonly optimizerCapabilities: readonly string[];
  readonly programKinds: readonly string[];
  readonly providerProfiles: readonly string[];
  readonly metricSets: readonly string[];
  readonly limits: Readonly<Record<string, number>>;
  readonly policyDigest: string;
  readonly humanInterventionRequired: false;
}

export interface DspyOptimizationRun {
  readonly tenantId: string;
  readonly runId: string;
  readonly state: string;
  readonly revision: number;
  readonly reasonCode: string;
  readonly specDigest: string;
  readonly artifact: Readonly<Record<string, unknown>> | null;
  readonly usage: Readonly<Record<string, number>> | null;
  readonly humanInterventionRequired: false;
}

@Injectable({ providedIn: 'root' })
export class DspyOptimizationApiService {
  private readonly core = inject(HubApiCoreService);

  capabilities(hubUrl: string): Observable<DspyOptimizationCapability> {
    const base = this.base(hubUrl);
    return this.core.request<unknown>('GET', `${base}/api/dspy-optimization/capabilities`, base, { timeoutMs: 8_000 })
      .pipe(map(parseCapability));
  }

  runs(hubUrl: string, tenantId: string): Observable<readonly DspyOptimizationRun[]> {
    if (!identifier(tenantId)) throw new Error('dspy_tenant_invalid');
    const base = this.base(hubUrl);
    return this.core.request<unknown>(
      'GET', `${base}/api/dspy-optimization/runs?tenant_id=${encodeURIComponent(tenantId)}&limit=100`, base,
      { timeoutMs: 8_000 },
    ).pipe(map(value => {
      const page = closed(value);
      if (!Array.isArray(page['items']) || page['items'].length > 100) throw new Error('dspy_runs_response_invalid');
      return Object.freeze(page['items'].map(parseRun));
    }));
  }

  cancel(hubUrl: string, run: DspyOptimizationRun): Observable<DspyOptimizationRun> {
    const base = this.base(hubUrl);
    return this.core.request<unknown>(
      'POST', `${base}/api/dspy-optimization/runs/${encodeURIComponent(run.runId)}/cancel`, base,
      { body: { tenant_id: run.tenantId, expected_revision: run.revision }, timeoutMs: 8_000 },
    ).pipe(map(parseRun));
  }

  dryRun(hubUrl: string, spec: Readonly<Record<string, unknown>>): Observable<Readonly<Record<string, unknown>>> {
    return this.postRecord(hubUrl, '/api/dspy-optimization/dry-run', { spec });
  }

  create(
    hubUrl: string, spec: Readonly<Record<string, unknown>>, idempotencyKey: string,
  ): Observable<DspyOptimizationRun> {
    if (!identifier(idempotencyKey)) throw new Error('dspy_idempotency_key_invalid');
    const base = this.base(hubUrl);
    return this.core.request<unknown>('POST', `${base}/api/dspy-optimization/runs`, base, {
      body: { spec, idempotency_key: idempotencyKey }, timeoutMs: 8_000,
    }).pipe(map(parseRun));
  }

  evaluate(
    hubUrl: string, baseline: Readonly<Record<string, unknown>>, candidate: Readonly<Record<string, unknown>>,
  ): Observable<Readonly<Record<string, unknown>>> {
    return this.postRecord(hubUrl, '/api/dspy-optimization/evaluations', { baseline, candidate });
  }

  promotePlan(
    hubUrl: string, plan: Readonly<Record<string, unknown>>, evaluation: Readonly<Record<string, unknown>>,
  ): Observable<Readonly<Record<string, unknown>>> {
    return this.postRecord(hubUrl, '/api/dspy-optimization/promotion-plans', { plan, evaluation });
  }

  rollback(
    hubUrl: string, tenantId: string, scopeId: string, expectedRevision: number,
  ): Observable<Readonly<Record<string, unknown>>> {
    if (!identifier(tenantId) || !identifier(scopeId) || !Number.isSafeInteger(expectedRevision) || expectedRevision < 1) {
      throw new Error('dspy_rollback_request_invalid');
    }
    return this.postRecord(hubUrl, '/api/dspy-optimization/rollbacks', {
      tenant_id: tenantId, scope_id: scopeId, expected_revision: expectedRevision,
    });
  }

  observability(hubUrl: string): Observable<Readonly<Record<string, unknown>>> {
    const base = this.base(hubUrl);
    return this.core.request<unknown>('GET', `${base}/api/dspy-optimization/observability`, base, { timeoutMs: 8_000 })
      .pipe(map(value => Object.freeze(closed(value))));
  }

  provenance(
    hubUrl: string, tenantId: string, scopeId: string,
  ): Observable<Readonly<Record<string, unknown>>> {
    if (!identifier(tenantId) || !identifier(scopeId)) throw new Error('dspy_provenance_request_invalid');
    const base = this.base(hubUrl);
    const query = `tenant_id=${encodeURIComponent(tenantId)}&scope_id=${encodeURIComponent(scopeId)}`;
    return this.core.request<unknown>('GET', `${base}/api/dspy-optimization/provenance?${query}`, base, { timeoutMs: 8_000 })
      .pipe(map(value => Object.freeze(closed(value))));
  }

  private postRecord(
    hubUrl: string, path: string, body: Readonly<Record<string, unknown>>,
  ): Observable<Readonly<Record<string, unknown>>> {
    const base = this.base(hubUrl);
    return this.core.request<unknown>('POST', `${base}${path}`, base, { body, timeoutMs: 8_000 })
      .pipe(map(value => Object.freeze(closed(value))));
  }

  private base(value: string): string {
    const candidate = normalizeHubOrigin(value);
    if (!candidate) throw new Error('dspy_hub_endpoint_denied');
    return candidate;
  }
}

function parseCapability(raw: unknown): DspyOptimizationCapability {
  const value = closed(raw);
  if (!['disabled', 'unavailable', 'degraded', 'available'].includes(String(value['state']))
      || value['human_intervention_required'] !== false || !Array.isArray(value['optimizer_capabilities'])
      || !Array.isArray(value['program_kinds'])) throw new Error('dspy_capability_response_invalid');
  const limits = closed(value['limits']);
  return Object.freeze({
    state: value['state'] as DspyOptimizationCapability['state'],
    reasonCode: String(value['reason_code']), mode: String(value['mode']),
    installedVersion: value['installed_version'] === null ? null : String(value['installed_version']),
    optimizerCapabilities: Object.freeze(value['optimizer_capabilities'].map(String)),
    programKinds: Object.freeze(value['program_kinds'].map(String)),
    providerProfiles: Object.freeze(Array.isArray(value['provider_profiles']) ? value['provider_profiles'].map(String) : []),
    metricSets: Object.freeze(Array.isArray(value['metric_sets']) ? value['metric_sets'].map(String) : []),
    limits: Object.freeze(Object.fromEntries(Object.entries(limits).map(([key, item]) => [key, Number(item)]))),
    policyDigest: String(value['policy_digest'] || ''),
    humanInterventionRequired: false,
  });
}

function parseRun(raw: unknown): DspyOptimizationRun {
  const value = closed(raw);
  if (!identifier(value['tenant_id']) || !identifier(value['run_id']) || !Number.isSafeInteger(value['revision'])
      || value['human_intervention_required'] !== false) throw new Error('dspy_run_response_invalid');
  return Object.freeze({
    tenantId: String(value['tenant_id']), runId: String(value['run_id']), state: String(value['state']),
    revision: Number(value['revision']), reasonCode: String(value['reason_code']), specDigest: String(value['spec_digest']),
    artifact: value['artifact'] === null ? null : Object.freeze(closed(value['artifact'])), humanInterventionRequired: false,
    usage: value['usage'] === null || value['usage'] === undefined
      ? null : Object.freeze(Object.fromEntries(Object.entries(closed(value['usage'])).map(([key, item]) => [key, Number(item)]))),
  });
}

function closed(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('dspy_api_response_invalid');
  return value as Record<string, unknown>;
}

function identifier(value: unknown): boolean {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$/.test(value);
}
