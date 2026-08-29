import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import { SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT, isAllowedSfuEndpoint } from './sfu-secure-endpoint.policy';

export interface DspyOptimizationCapability {
  readonly state: 'disabled' | 'unavailable' | 'degraded' | 'available';
  readonly reasonCode: string;
  readonly mode: string;
  readonly installedVersion: string | null;
  readonly optimizerCapabilities: readonly string[];
  readonly programKinds: readonly string[];
  readonly limits: Readonly<Record<string, number>>;
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
  readonly humanInterventionRequired: false;
}

@Injectable({ providedIn: 'root' })
export class DspyOptimizationApiService {
  private readonly core = inject(HubApiCoreService);
  private readonly allowLocalhost = inject(SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT);

  capabilities(hubUrl: string): Observable<DspyOptimizationCapability> {
    const base = this.base(hubUrl);
    return this.core.request<unknown>('GET', `${base}/api/dspy-optimization/capabilities`, base, { timeoutMs: 8_000 })
      .pipe(map(value => parseCapability(envelopeData(value))));
  }

  runs(hubUrl: string, tenantId: string): Observable<readonly DspyOptimizationRun[]> {
    if (!identifier(tenantId)) throw new Error('dspy_tenant_invalid');
    const base = this.base(hubUrl);
    return this.core.request<unknown>(
      'GET', `${base}/api/dspy-optimization/runs?tenant_id=${encodeURIComponent(tenantId)}&limit=100`, base,
      { timeoutMs: 8_000 },
    ).pipe(map(value => {
      const page = closed(envelopeData(value));
      if (!Array.isArray(page['items']) || page['items'].length > 100) throw new Error('dspy_runs_response_invalid');
      return Object.freeze(page['items'].map(parseRun));
    }));
  }

  cancel(hubUrl: string, run: DspyOptimizationRun): Observable<DspyOptimizationRun> {
    const base = this.base(hubUrl);
    return this.core.request<unknown>(
      'POST', `${base}/api/dspy-optimization/runs/${encodeURIComponent(run.runId)}/cancel`, base,
      { body: { tenant_id: run.tenantId, expected_revision: run.revision }, timeoutMs: 8_000 },
    ).pipe(map(value => parseRun(envelopeData(value))));
  }

  private base(value: string): string {
    const candidate = String(value || '').replace(/\/+$/, '');
    if (!isAllowedSfuEndpoint(candidate, 'http', this.allowLocalhost)) throw new Error('dspy_hub_endpoint_denied');
    return candidate;
  }
}

function envelopeData(raw: unknown): unknown {
  const value = closed(raw);
  if (value['status'] !== 'success' || !('data' in value)) throw new Error('dspy_api_response_invalid');
  return value['data'];
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
    limits: Object.freeze(Object.fromEntries(Object.entries(limits).map(([key, item]) => [key, Number(item)]))),
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
  });
}

function closed(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('dspy_api_response_invalid');
  return value as Record<string, unknown>;
}

function identifier(value: unknown): boolean {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$/.test(value);
}
