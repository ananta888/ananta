import { computed, inject, Injectable, signal } from '@angular/core';
import { finalize, forkJoin } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  MODEL_ANALYSIS_MAX_GRAPH_EDGES,
  MODEL_ANALYSIS_MAX_GRAPH_NODES,
  ModelAnalysisApiClient,
} from './model-analysis-api.client';
import {
  ModelAnalysisCapabilities,
  ModelAnalysisGraph,
  ModelAnalysisGraphEdge,
  ModelAnalysisGraphNode,
  ModelAnalysisJob,
  ModelAnalysisJobPage,
  ModelAnalysisJobStatus,
  ModelAnalysisReport,
  ModelAnalysisReportSection,
  ModelAnalysisSectionStatus,
  ModelAnalysisViewState,
  StartModelAnalysisRequest,
} from './model-analysis.models';

const SECTION_STATUSES = new Set<ModelAnalysisSectionStatus>([
  'available',
  'unsupported',
  'not_run',
  'failed',
]);
const JOB_STATUSES = new Set<ModelAnalysisJobStatus>([
  'queued',
  'claimed',
  'running',
  'cancel_requested',
  'completed',
  'failed',
  'cancelled',
]);

@Injectable()
export class ModelAnalysisFacade {
  private readonly api = inject(ModelAnalysisApiClient);
  private readonly directory = inject(AgentDirectoryService);

  readonly hubUrl = signal('');
  readonly capabilities = signal<ModelAnalysisCapabilities | null>(null);
  readonly jobs = signal<readonly ModelAnalysisJob[]>([]);
  readonly nextCursor = signal<string | null>(null);
  readonly selectedJob = signal<ModelAnalysisJob | null>(null);
  readonly report = signal<ModelAnalysisReport | null>(null);
  readonly graph = signal<ModelAnalysisGraph | null>(null);
  readonly loadingOverview = signal(false);
  readonly loadingSelection = signal(false);
  readonly mutating = signal(false);
  readonly loaded = signal(false);
  readonly permissionDenied = signal(false);
  readonly error = signal('');
  readonly errorReasonCode = signal('');

  readonly viewState = computed<ModelAnalysisViewState>(() => {
    if (this.loadingOverview() && !this.loaded()) return 'loading';
    if (this.permissionDenied()) return 'permission';
    if (this.error()) return 'error';
    if (this.capabilities() && !this.capabilities()!.supported) {
      return 'unsupported';
    }
    if (this.loaded() && this.jobs().length === 0) return 'empty';
    return 'ready';
  });
  readonly stateReasonCode = computed(() => {
    switch (this.viewState()) {
      case 'loading': return 'model_analysis_loading';
      case 'empty': return 'model_analysis_jobs_empty';
      case 'unsupported':
        return this.capabilities()?.reason_code || 'model_analysis_unsupported';
      case 'permission':
        return this.errorReasonCode() || 'model_analysis_permission_denied';
      case 'error':
        return this.errorReasonCode() || 'model_analysis_request_failed';
      default:
        return 'model_analysis_available';
    }
  });

  constructor() {
    this.resolveHub();
  }

  loadOverview(): void {
    const hubUrl = this.resolveHub();
    if (!hubUrl) {
      this.captureError(
        new Error('model_analysis_hub_unconfigured'),
        'Kein Hub für die Modellanalyse konfiguriert.',
      );
      return;
    }
    this.resetError();
    this.loadingOverview.set(true);
    forkJoin({
      capabilities: this.api.capabilities(hubUrl),
      jobs: this.api.listJobs(hubUrl, '', 50),
    }).pipe(
      finalize(() => this.loadingOverview.set(false)),
    ).subscribe({
      next: ({ capabilities, jobs }) => {
        this.capabilities.set(normalizeCapabilities(capabilities));
        const page = normalizeJobPage(jobs);
        this.jobs.set(page.items);
        this.nextCursor.set(page.next_cursor);
        this.loaded.set(true);
      },
      error: error => this.captureError(
        error,
        'Modellanalyse konnte nicht geladen werden.',
      ),
    });
  }

  loadMore(): void {
    const cursor = this.nextCursor();
    const hubUrl = this.hubUrl();
    if (!cursor || !hubUrl || this.loadingOverview()) return;
    this.loadingOverview.set(true);
    this.api.listJobs(hubUrl, cursor, 50).pipe(
      finalize(() => this.loadingOverview.set(false)),
    ).subscribe({
      next: value => {
        const page = normalizeJobPage(value);
        const known = new Set(this.jobs().map(job => job.job_id));
        this.jobs.update(current => [
          ...current,
          ...page.items.filter(job => !known.has(job.job_id)),
        ]);
        this.nextCursor.set(page.next_cursor);
      },
      error: error => this.captureError(
        error,
        'Weitere Analysejobs konnten nicht geladen werden.',
      ),
    });
  }

  start(importRef: string): void {
    const normalizedRef = normalizeImportRef(importRef);
    if (!normalizedRef) {
      this.error.set(
        'Importreferenz muss 1 bis 512 sichere Zeichen enthalten und darf keine Pfadnavigation verwenden.',
      );
      return;
    }
    const hubUrl = this.hubUrl() || this.resolveHub();
    if (!hubUrl || this.mutating()) return;
    this.resetError();
    this.mutating.set(true);
    const request: StartModelAnalysisRequest = {
      import_ref: normalizedRef,
      analysis_kind: 'full',
      profile_id: 'bounded-ui',
      requested_artifact_kinds: ['report', 'model_graph'],
    };
    this.api.startJob(
      hubUrl,
      request,
      idempotencyKey('model-analysis-start'),
    ).pipe(
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: value => {
        const job = normalizeJob(value);
        this.jobs.update(current => [
          job,
          ...current.filter(item => item.job_id !== job.job_id),
        ]);
        this.selectJob(job.job_id);
      },
      error: error => this.captureError(
        error,
        'Analysejob konnte nicht gestartet werden.',
      ),
    });
  }

  selectJob(jobId: string): void {
    const normalizedId = normalizeEntityId(jobId);
    const hubUrl = this.hubUrl();
    if (!normalizedId || !hubUrl) return;
    this.resetError();
    this.loadingSelection.set(true);
    this.report.set(null);
    this.graph.set(null);
    this.api.getJob(hubUrl, normalizedId).pipe(
      finalize(() => this.loadingSelection.set(false)),
    ).subscribe({
      next: value => {
        const job = normalizeJob(value);
        this.selectedJob.set(job);
        this.replaceJob(job);
        if (job.status === 'completed') this.loadCompletedAssets(job.job_id);
      },
      error: error => this.captureError(
        error,
        'Analysestatus konnte nicht geladen werden.',
      ),
    });
  }

  refreshSelected(): void {
    const selected = this.selectedJob();
    if (selected) this.selectJob(selected.job_id);
  }

  cancelSelected(): void {
    const selected = this.selectedJob();
    const hubUrl = this.hubUrl();
    if (!selected || !hubUrl || !isCancellableStatus(selected.status)) return;
    this.resetError();
    this.mutating.set(true);
    this.api.cancelJob(
      hubUrl,
      selected.job_id,
      idempotencyKey('model-analysis-cancel'),
    ).pipe(
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: value => {
        const job = normalizeJob(value);
        this.selectedJob.set(job);
        this.replaceJob(job);
      },
      error: error => this.captureError(
        error,
        'Analysejob konnte nicht abgebrochen werden.',
      ),
    });
  }

  resetError(): void {
    this.error.set('');
    this.errorReasonCode.set('');
    this.permissionDenied.set(false);
  }

  private loadCompletedAssets(jobId: string): void {
    const hubUrl = this.hubUrl();
    if (!hubUrl) return;
    this.loadingSelection.set(true);
    forkJoin({
      report: this.api.getReport(hubUrl, jobId),
      graph: this.api.getGraph(hubUrl, jobId),
    }).pipe(
      finalize(() => this.loadingSelection.set(false)),
    ).subscribe({
      next: ({ report, graph }) => {
        this.report.set(normalizeReport(report));
        this.graph.set(normalizeGraph(graph));
      },
      error: error => this.captureError(
        error,
        'Report oder Modellgraph konnte nicht geladen werden.',
      ),
    });
  }

  private replaceJob(job: ModelAnalysisJob): void {
    this.jobs.update(current => current.map(
      item => item.job_id === job.job_id ? job : item,
    ));
  }

  private resolveHub(): string {
    const entry = this.directory.list().find(agent => agent.role === 'hub')
      || this.directory.list().find(agent => agent.name === 'hub');
    const url = String(entry?.url || '').replace(/\/+$/, '');
    this.hubUrl.set(url);
    return url;
  }

  private captureError(error: unknown, fallback: string): void {
    const raw = asRecord(error);
    const body = asRecord(raw?.['error']);
    const envelope = asRecord(body?.['data']) || body;
    const status = Number(
      (error as { status?: unknown } | null)?.status
      || envelope?.['status']
      || 0,
    );
    this.errorReasonCode.set(
      normalizeReasonCode(
        envelope?.['reason_code']
        || envelope?.['code']
        || (status === 401 || status === 403
          ? 'model_analysis_permission_denied'
          : 'model_analysis_request_failed'),
      ),
    );
    this.permissionDenied.set(status === 401 || status === 403);
    this.error.set(this.permissionDenied() ? '' : boundedError(error, fallback));
  }
}

export function normalizeCapabilities(value: unknown): ModelAnalysisCapabilities {
  const raw = asRecord(unwrap(value));
  const maxNodes = boundedInteger(
    raw?.['max_graph_nodes'],
    MODEL_ANALYSIS_MAX_GRAPH_NODES,
    MODEL_ANALYSIS_MAX_GRAPH_NODES,
  );
  const maxEdges = boundedInteger(
    raw?.['max_graph_edges'],
    MODEL_ANALYSIS_MAX_GRAPH_EDGES,
    MODEL_ANALYSIS_MAX_GRAPH_EDGES,
  );
  return {
    supported: Boolean(raw?.['supported'] ?? raw?.['available']),
    reason_code: optionalText(raw?.['reason_code']),
    max_graph_nodes: maxNodes,
    max_graph_edges: maxEdges,
  };
}

export function normalizeJobPage(value: unknown): ModelAnalysisJobPage {
  const unwrapped = unwrap(value);
  const raw = asRecord(unwrapped);
  const source = Array.isArray(unwrapped)
    ? unwrapped
    : Array.isArray(raw?.['items'])
      ? raw!['items']
      : Array.isArray(raw?.['jobs'])
        ? raw!['jobs']
        : [];
  return {
    items: source.map(normalizeJob),
    next_cursor: optionalText(raw?.['next_cursor']) || null,
  };
}

export function normalizeJob(value: unknown): ModelAnalysisJob {
  const raw = asRecord(unwrap(value)) || {};
  const statusValue = String(raw['status'] || '').toLowerCase() as ModelAnalysisJobStatus;
  return {
    schema: optionalText(raw['schema']),
    job_id: normalizeEntityId(raw['job_id'] ?? raw['id']) || 'unknown-job',
    hub_task_id: optionalText(raw['hub_task_id']),
    model_id: boundedText(raw['model_id'], 512) || 'pending-model',
    import_ref: optionalText(raw['import_ref']),
    analysis_kind: boundedText(raw['analysis_kind'], 128) || 'full',
    profile_id: boundedText(raw['profile_id'], 128) || 'bounded-ui',
    request_sha256: optionalText(raw['request_sha256']),
    requested_artifact_kinds: stringArray(raw['requested_artifact_kinds'], 32),
    max_runtime_seconds: optionalPositiveInteger(raw['max_runtime_seconds']),
    max_output_bytes: optionalPositiveInteger(raw['max_output_bytes']),
    status: JOB_STATUSES.has(statusValue) ? statusValue : 'unknown',
    progress_percent: boundedInteger(raw['progress_percent'], 0, 100),
    reason_code: optionalText(raw['reason_code']),
    created_at: optionalText(raw['created_at']),
    updated_at: optionalText(raw['updated_at']),
  };
}

export function normalizeReport(value: unknown): ModelAnalysisReport {
  const raw = asRecord(unwrap(value)) || {};
  const sectionSource = Array.isArray(raw['sections']) ? raw['sections'] : [];
  const sections: ModelAnalysisReportSection[] = sectionSource.slice(0, 100).map(item => {
    const section = asRecord(item) || {};
    const rawStatus = String(section['status'] || '') as ModelAnalysisSectionStatus;
    return {
      name: boundedText(section['name'], 128) || 'unnamed',
      status: SECTION_STATUSES.has(rawStatus) ? rawStatus : 'failed',
      reason_code: SECTION_STATUSES.has(rawStatus)
        ? optionalText(section['reason_code'])
          || (rawStatus === 'unsupported'
            ? 'analysis_section_unsupported'
            : rawStatus === 'not_run'
              ? 'analysis_section_not_run'
              : rawStatus === 'failed'
                ? 'analysis_section_failed'
                : undefined)
        : 'invalid_section_status',
      data: section['data'] ?? null,
    };
  });
  return {
    schema: boundedText(raw['schema'], 128) || 'unknown',
    content_digest: boundedText(
      raw['content_digest'] ?? raw['digest'],
      80,
    ),
    sections,
  };
}

export function normalizeGraph(value: unknown): ModelAnalysisGraph {
  const raw = asRecord(unwrap(value)) || {};
  const sourceNodes = Array.isArray(raw['nodes']) ? raw['nodes'] : [];
  const sourceEdges = Array.isArray(raw['edges']) ? raw['edges'] : [];
  const nodes: ModelAnalysisGraphNode[] = sourceNodes
    .slice(0, MODEL_ANALYSIS_MAX_GRAPH_NODES)
    .map((value, index) => {
      const node = asRecord(value) || {};
      return {
        node_id: boundedText(node['node_id'] ?? node['id'], 256) || `node-${index}`,
        label: boundedText(node['label'] ?? node['name'], 256) || `Knoten ${index + 1}`,
        kind: boundedText(node['kind'], 64) || 'unknown',
      };
    });
  const knownNodes = new Set(nodes.map(node => node.node_id));
  const edges: ModelAnalysisGraphEdge[] = sourceEdges
    .slice(0, MODEL_ANALYSIS_MAX_GRAPH_EDGES)
    .map((value, index) => {
      const edge = asRecord(value) || {};
      return {
        edge_id: boundedText(edge['edge_id'] ?? edge['id'], 256) || `edge-${index}`,
        source_node_id: boundedText(
          edge['source_node_id'] ?? edge['source'],
          256,
        ),
        target_node_id: boundedText(
          edge['target_node_id'] ?? edge['target'],
          256,
        ),
        kind: boundedText(edge['kind'], 64) || 'related',
      };
    })
    .filter(edge => knownNodes.has(edge.source_node_id) && knownNodes.has(edge.target_node_id));
  return {
    schema: boundedText(raw['schema'], 128) || 'unknown',
    nodes,
    edges,
    truncated: Boolean(raw['truncated'])
      || sourceNodes.length > nodes.length
      || sourceEdges.length > edges.length,
  };
}

export function isCancellableStatus(status: ModelAnalysisJobStatus): boolean {
  return ['queued', 'claimed', 'running'].includes(status);
}

function unwrap(value: unknown): unknown {
  let current = value;
  for (let index = 0; index < 4; index += 1) {
    const record = asRecord(current);
    if (record && 'data' in record && ('status' in record || 'success' in record)) {
      current = record['data'];
    } else {
      break;
    }
  }
  return current;
}

function asRecord(value: unknown): Record<string, any> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, any>
    : null;
}

function normalizeImportRef(value: unknown): string {
  const candidate = String(value || '').trim();
  if (
    !/^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,511}$/.test(candidate)
    || candidate.split('/').some(part => part === '..')
  ) {
    return '';
  }
  return candidate;
}

function normalizeEntityId(value: unknown): string {
  const candidate = String(value || '').trim();
  return /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(candidate)
    ? candidate
    : '';
}

function normalizeReasonCode(value: unknown): string {
  const candidate = String(value || '').trim().toLowerCase();
  return /^[a-z][a-z0-9_]{0,127}$/.test(candidate)
    ? candidate
    : 'model_analysis_request_failed';
}

function optionalText(value: unknown): string | undefined {
  const text = boundedText(value, 512);
  return text || undefined;
}

function boundedText(value: unknown, limit: number): string {
  const text = String(value ?? '')
    .replace(/(bearer\s+)[a-z0-9._~+/=-]+/gi, '$1[REDACTED]')
    .replace(/((?:api[_-]?key|token|secret|password)\s*[:=]\s*)\S+/gi, '$1[REDACTED]');
  return text.slice(0, limit);
}

function boundedError(error: unknown, fallback: string): string {
  const raw = asRecord(error);
  const body = asRecord(raw?.['error']);
  return boundedText(
    body?.['message']
    || body?.['reason_code']
    || raw?.['message']
    || fallback,
    700,
  );
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  const number = Number(value);
  return Number.isSafeInteger(number)
    ? Math.min(maximum, Math.max(0, number))
    : fallback;
}

function optionalPositiveInteger(value: unknown): number | undefined {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : undefined;
}

function stringArray(value: unknown, maximum: number): readonly string[] {
  return Array.isArray(value)
    ? value.slice(0, maximum).map(item => boundedText(item, 128)).filter(Boolean)
    : [];
}

function idempotencyKey(prefix: string): string {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}
