import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { map, catchError } from 'rxjs/operators';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { SourceControlV1ApiClient } from '../../../services/source-control-v1-api.client';
import {
  CodeCompassGraphV1,
} from '../../../models/source-control-contracts';

export interface AnantaTemplate {
  id: string;
  name: string;
  description: string;
  category: 'scrum' | 'kanban' | 'opencode' | 'system';
  prompt_template: string;
  is_seed: boolean;
}

export interface VpPreset {
  id: string;
  name: string;
  description: string;
  tags: string[];
}

export interface VpSkillProfile {
  id: string;
  name: string;
  description: string;
  role: string;
  task_kinds: string[];
  capabilities: string[];
  tags: string[];
}

export interface VpStepPosition { x: number; y: number; }
export interface VpArtifactRef {
  name: string;
  kind: string;
  required: boolean;
  description: string;
  produced_by_step?: string | null;
  produced_by_output?: string | null;
}
export interface VpStepIo { inputs: VpArtifactRef[]; outputs: VpArtifactRef[]; }
export interface VpLoopPolicy { kind: string; max_iterations: number; condition: string | null; }
export interface VpTransitionCondition { kind: string; expression: string | null; output_name: string | null; loop_policy: VpLoopPolicy | null; }
export interface VpEdge { id: string; source: string; target: string; condition: VpTransitionCondition; label: string | null; metadata: Record<string, unknown>; }
export interface VpStep { id: string; label: string; kind: string; role: string | null; agent_skill_profile_id: string | null; io: VpStepIo; position: VpStepPosition; gate: boolean; policy_hints: string[]; metadata: Record<string, unknown>; }
export interface VpGraph { id: string; name: string; description: string; steps: VpStep[]; edges: VpEdge[]; tags: string[]; metadata: Record<string, unknown>; }

export interface VpDryRunResult {
  dry_run: boolean;
  validation: { valid: boolean; errors: string[]; warnings: string[] };
  policy_summary: Record<string, unknown>;
  blueprint: unknown;
  step_count: number;
  edge_count: number;
}

export interface AnantaWorker {
  url: string;
  name: string;
  role: string;
  status: 'online' | 'offline' | 'degraded';
  worker_roles: string[];
  capabilities: string[];
}

export interface AutopilotStatus {
  running: boolean;
  goal: string;
  team_id: string;
  started_at: number | null;
  tick_count: number;
  dispatched_count: number;
  completed_count: number;
  failed_count: number;
  last_error: string | null;
  effective_security_policy: {
    level: string;
    max_concurrency_cap: number;
    allowed_tool_classes: string[];
  };
  circuit_breakers: {
    open_workers: string[];
    open_count: number;
    failure_streak: Record<string, number>;
  };
}

export interface CodeCompassGraphWindowRequest {
  readonly limit: number;
  readonly maxEdges: number;
  readonly domainScope?: string;
  readonly includeSubdomains?: boolean;
}

export type CodeCompassGraphStage = 'nodes' | 'edges';

export type CodeCompassSemanticScopeStatus = 'complete' | 'partial' | 'unavailable';

/**
 * Server-verified state of the semantic supplement for one staged scope.
 * `null` on the staged page means a legacy response: transport can still be
 * complete, but semantic completeness has not been proven.
 */
export interface CodeCompassSemanticScopeEvidence {
  readonly status: CodeCompassSemanticScopeStatus;
  readonly complete: boolean;
  /** Opaque selector echoed from the staged request. */
  readonly scopeKey: string | null;
  /** Worker partition identities; intentionally distinct from the selector. */
  readonly supplementDomainKeys: readonly string[];
  readonly sourceRevisionId: string;
  readonly sourceRevisionDigest: string;
  readonly graphRevision: string;
  readonly supplementNodeCount: number;
  readonly supplementEdgeCount: number;
  readonly supplementDeclarationCount: number;
}

export interface CodeCompassGraphStagedPageRequest {
  readonly stage: CodeCompassGraphStage;
  readonly cursor?: string;
  readonly pageSize: number;
  readonly domainScope?: string;
  readonly includeSubdomains?: boolean;
}

export interface CodeCompassGraphStagedPage {
  readonly graph: CodeCompassGraphV1;
  readonly stage: CodeCompassGraphStage;
  readonly nextCursor: string | null;
  readonly returned: number;
  readonly total: number;
  readonly graphRevision: string;
  readonly evidenceRevision: string | null;
  readonly semanticScope: Readonly<CodeCompassSemanticScopeEvidence> | null;
  readonly complete: boolean;
}

export interface CodeCompassGraphDomainFacet {
  readonly key: string;
  readonly label: string;
  readonly parentKey: string | null;
  readonly depth: number;
  readonly directNodeCount: number;
  readonly subtreeNodeCount: number;
  readonly hasChildren: boolean;
  readonly source: string;
  readonly path: string;
  /** Additive top-level supplement counts; absent on subfacets is unknown, never zero. */
  readonly baseNodeCount?: number;
  readonly semanticNodeCount?: number;
  readonly completeNodeCount?: number;
  readonly semanticEdgeCount?: number;
  readonly declarationEdgeCount?: number;
  readonly semanticScopeStatus?: string;
}

export interface CodeCompassGraphInventoryPage {
  readonly domains: readonly CodeCompassGraphDomainFacet[];
  readonly nextCursor: string | null;
  readonly totalDomains: number;
  readonly totalNodes: number;
  readonly totalEdges: number;
  readonly graphRevision: string;
}

export class CodeCompassGraphInventoryContractError extends Error {
  constructor(readonly reasonCode: string) {
    super(reasonCode);
    this.name = 'CodeCompassGraphInventoryContractError';
  }
}

export class CodeCompassGraphStagedPageContractError extends Error {
  constructor(readonly reasonCode: string) {
    super(reasonCode);
    this.name = 'CodeCompassGraphStagedPageContractError';
  }
}

function inventoryRecord(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new CodeCompassGraphInventoryContractError(`${path}_invalid`);
  }
  return value as Record<string, unknown>;
}

function inventoryText(value: unknown, path: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new CodeCompassGraphInventoryContractError(`${path}_invalid`);
  }
  return value.trim();
}

function inventoryNullableText(value: unknown, path: string): string | null {
  if (value === null) return null;
  return inventoryText(value, path);
}

function inventoryCount(value: unknown, path: string): number {
  if (
    typeof value !== 'number'
    || !Number.isFinite(value)
    || !Number.isInteger(value)
    || value < 0
  ) {
    throw new CodeCompassGraphInventoryContractError(`${path}_invalid`);
  }
  return value;
}

function optionalInventoryCount(
  record: Record<string, unknown>,
  field: string,
  path: string,
): number | undefined {
  return Object.prototype.hasOwnProperty.call(record, field)
    ? inventoryCount(record[field], `${path}.${field}`)
    : undefined;
}

function optionalInventoryText(
  record: Record<string, unknown>,
  field: string,
  path: string,
): string | undefined {
  return Object.prototype.hasOwnProperty.call(record, field)
    ? inventoryText(record[field], `${path}.${field}`)
    : undefined;
}

export function parseCodeCompassGraphInventoryPage(
  raw: unknown,
): CodeCompassGraphInventoryPage {
  const root = inventoryRecord(raw, 'graph_inventory');
  if (inventoryText(root['schema'], 'graph_inventory.schema') !== 'codecompass_graph_inventory.v1') {
    throw new CodeCompassGraphInventoryContractError('graph_inventory.schema_unsupported');
  }
  const facets = inventoryRecord(root['facets'], 'graph_inventory.facets');
  const domainPage = inventoryRecord(facets['domains'], 'graph_inventory.facets.domains');
  const metadata = inventoryRecord(root['metadata'], 'graph_inventory.metadata');
  const rawItems = domainPage['items'];
  if (!Array.isArray(rawItems)) {
    throw new CodeCompassGraphInventoryContractError(
      'graph_inventory.facets.domains.items_invalid',
    );
  }
  const domains = rawItems.map((item, index): CodeCompassGraphDomainFacet => {
    const path = `graph_inventory.facets.domains.items[${index}]`;
    const value = inventoryRecord(item, path);
    const directNodeCount = inventoryCount(
      value['direct_node_count'],
      `${path}.direct_node_count`,
    );
    const subtreeNodeCount = inventoryCount(
      value['subtree_node_count'],
      `${path}.subtree_node_count`,
    );
    if (directNodeCount > subtreeNodeCount) {
      throw new CodeCompassGraphInventoryContractError(`${path}.node_counts_inconsistent`);
    }
    if (typeof value['has_children'] !== 'boolean') {
      throw new CodeCompassGraphInventoryContractError(`${path}.has_children_invalid`);
    }
    const baseNodeCount = optionalInventoryCount(value, 'base_node_count', path);
    const semanticNodeCount = optionalInventoryCount(value, 'semantic_node_count', path);
    const completeNodeCount = optionalInventoryCount(value, 'complete_node_count', path);
    if (
      baseNodeCount !== undefined
      && semanticNodeCount !== undefined
      && completeNodeCount !== undefined
      && completeNodeCount !== baseNodeCount + semanticNodeCount
    ) {
      throw new CodeCompassGraphInventoryContractError(
        `${path}.complete_node_count_inconsistent`,
      );
    }
    const semanticEdgeCount = optionalInventoryCount(value, 'semantic_edge_count', path);
    const declarationEdgeCount = optionalInventoryCount(value, 'declaration_edge_count', path);
    const semanticScopeStatus = optionalInventoryText(value, 'semantic_scope_status', path);
    return {
      key: inventoryText(value['key'], `${path}.key`),
      label: inventoryText(value['label'], `${path}.label`),
      parentKey: inventoryNullableText(value['parent_key'], `${path}.parent_key`),
      depth: inventoryCount(value['depth'], `${path}.depth`),
      directNodeCount,
      subtreeNodeCount,
      hasChildren: value['has_children'],
      source: inventoryText(value['source'], `${path}.source`),
      path: inventoryText(value['path'], `${path}.path`),
      ...(baseNodeCount === undefined ? {} : { baseNodeCount }),
      ...(semanticNodeCount === undefined ? {} : { semanticNodeCount }),
      ...(completeNodeCount === undefined ? {} : { completeNodeCount }),
      ...(semanticEdgeCount === undefined ? {} : { semanticEdgeCount }),
      ...(declarationEdgeCount === undefined ? {} : { declarationEdgeCount }),
      ...(semanticScopeStatus === undefined ? {} : { semanticScopeStatus }),
    };
  });
  const totalDomains = inventoryCount(
    domainPage['total_count'],
    'graph_inventory.facets.domains.total_count',
  );
  if (domains.length > totalDomains) {
    throw new CodeCompassGraphInventoryContractError(
      'graph_inventory.facets.domains.total_count_inconsistent',
    );
  }
  return {
    domains,
    nextCursor: inventoryNullableText(
      domainPage['next_cursor'],
      'graph_inventory.facets.domains.next_cursor',
    ),
    totalDomains,
    totalNodes: inventoryCount(metadata['total_nodes'], 'graph_inventory.metadata.total_nodes'),
    totalEdges: inventoryCount(metadata['total_edges'], 'graph_inventory.metadata.total_edges'),
    graphRevision: inventoryText(root['graph_revision'], 'graph_inventory.graph_revision'),
  };
}

export function parseCodeCompassGraphStagedPage(
  raw: unknown,
  expectedStage: CodeCompassGraphStage,
): CodeCompassGraphStagedPage {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_page_invalid');
  }
  const graph = raw as unknown as CodeCompassGraphV1;
  const metadata = graph.metadata;
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_metadata_invalid');
  }
  if (metadata['view'] !== 'staged' || metadata['stage'] !== expectedStage) {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_stage_mismatch');
  }
  if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_records_invalid');
  }
  if (
    (expectedStage === 'nodes' && graph.edges.length !== 0)
    || (expectedStage === 'edges' && graph.nodes.length !== 0)
  ) {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_foreign_records_present');
  }
  const records = expectedStage === 'nodes' ? graph.nodes : graph.edges;
  records.forEach((record, index) => {
    if (!record || typeof record !== 'object' || Array.isArray(record)) {
      throw new CodeCompassGraphStagedPageContractError(
        `graph_staged_${expectedStage}[${index}]_invalid`,
      );
    }
    const id = expectedStage === 'nodes'
      ? record['node_id']
      : record['edge_id'];
    if (typeof id !== 'string' || !id.trim()) {
      throw new CodeCompassGraphStagedPageContractError(
        `graph_staged_${expectedStage}[${index}]_id_invalid`,
      );
    }
    if (expectedStage === 'edges') {
      for (const endpoint of ['source_id', 'target_id']) {
        const value = record[endpoint];
        if (typeof value !== 'string' || !value.trim()) {
          throw new CodeCompassGraphStagedPageContractError(
            `graph_staged_edges[${index}].${endpoint}_invalid`,
          );
        }
      }
    }
  });
  const returned = stagedPageCount(metadata['delivery_returned'], 'delivery_returned');
  const total = stagedPageCount(metadata['delivery_total'], 'delivery_total');
  if (returned !== records.length || returned > total) {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_delivery_count_mismatch');
  }
  const rawCursor = metadata['next_cursor'];
  if (rawCursor !== null && (typeof rawCursor !== 'string' || !rawCursor.trim())) {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_cursor_invalid');
  }
  if (typeof metadata['delivery_complete'] !== 'boolean') {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_complete_invalid');
  }
  const complete = metadata['delivery_complete'];
  const nextCursor = typeof rawCursor === 'string' ? rawCursor.trim() : null;
  if (complete !== (nextCursor === null)) {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_completion_mismatch');
  }
  const rawRevision = metadata['content_graph_revision'];
  if (typeof rawRevision !== 'string' || !rawRevision.trim()) {
    throw new CodeCompassGraphStagedPageContractError('graph_staged_revision_invalid');
  }
  const rawEvidenceRevision = metadata['evidence_graph_revision'];
  if (
    rawEvidenceRevision !== undefined
    && (typeof rawEvidenceRevision !== 'string' || !rawEvidenceRevision.trim())
  ) {
    throw new CodeCompassGraphStagedPageContractError(
      'graph_staged_evidence_revision_invalid',
    );
  }
  return {
    graph,
    stage: expectedStage,
    nextCursor,
    returned,
    total,
    graphRevision: rawRevision.trim(),
    evidenceRevision: typeof rawEvidenceRevision === 'string'
      ? rawEvidenceRevision.trim()
      : null,
    semanticScope: stagedSemanticScopeEvidence(metadata),
    complete,
  };
}

const SEMANTIC_SCOPE_METADATA_FIELDS = Object.freeze([
  'semantic_scope_status',
  'semantic_scope_complete',
  'semantic_scope_key',
  'semantic_scope_supplement_domain_keys',
  'semantic_scope_source_revision_id',
  'semantic_scope_source_revision_digest',
  'semantic_scope_graph_revision',
  'semantic_scope_supplement_node_count',
  'semantic_scope_supplement_edge_count',
  'semantic_scope_supplement_declaration_count',
] as const);

function stagedSemanticScopeEvidence(
  metadata: Record<string, unknown>,
): Readonly<CodeCompassSemanticScopeEvidence> | null {
  const hasEvidence = SEMANTIC_SCOPE_METADATA_FIELDS.some(field => (
    Object.prototype.hasOwnProperty.call(metadata, field)
  ));
  if (!hasEvidence) return null;

  const status = metadata['semantic_scope_status'];
  if (status !== 'complete' && status !== 'partial' && status !== 'unavailable') {
    throw new CodeCompassGraphStagedPageContractError(
      'graph_staged_semantic_scope_status_invalid',
    );
  }
  const complete = metadata['semantic_scope_complete'];
  if (typeof complete !== 'boolean' || complete !== (status === 'complete')) {
    throw new CodeCompassGraphStagedPageContractError(
      'graph_staged_semantic_scope_completion_invalid',
    );
  }
  const rawScopeKey = metadata['semantic_scope_key'];
  const scopeKey = rawScopeKey === null
    ? null
    : stagedSemanticScopeText(rawScopeKey, 'key');
  const rawSupplementDomainKeys = metadata['semantic_scope_supplement_domain_keys'];
  if (!Array.isArray(rawSupplementDomainKeys)) {
    throw new CodeCompassGraphStagedPageContractError(
      'graph_staged_semantic_scope_supplement_domain_keys_invalid',
    );
  }
  const supplementDomainKeys = rawSupplementDomainKeys.map((value, index) => (
    stagedSemanticScopeText(value, `supplement_domain_keys[${index}]`)
  ));
  if (new Set(supplementDomainKeys).size !== supplementDomainKeys.length) {
    throw new CodeCompassGraphStagedPageContractError(
      'graph_staged_semantic_scope_supplement_domain_keys_duplicate',
    );
  }
  if (complete && (scopeKey === null || supplementDomainKeys.length === 0)) {
    throw new CodeCompassGraphStagedPageContractError(
      'graph_staged_semantic_scope_completion_proof_invalid',
    );
  }
  return Object.freeze({
    status,
    complete,
    scopeKey,
    supplementDomainKeys: Object.freeze(supplementDomainKeys),
    sourceRevisionId: stagedSemanticScopeText(
      metadata['semantic_scope_source_revision_id'],
      'source_revision_id',
    ),
    sourceRevisionDigest: stagedSemanticScopeText(
      metadata['semantic_scope_source_revision_digest'],
      'source_revision_digest',
    ),
    graphRevision: stagedSemanticScopeText(
      metadata['semantic_scope_graph_revision'],
      'graph_revision',
    ),
    supplementNodeCount: stagedPageCount(
      metadata['semantic_scope_supplement_node_count'],
      'semantic_scope_supplement_node_count',
    ),
    supplementEdgeCount: stagedPageCount(
      metadata['semantic_scope_supplement_edge_count'],
      'semantic_scope_supplement_edge_count',
    ),
    supplementDeclarationCount: stagedPageCount(
      metadata['semantic_scope_supplement_declaration_count'],
      'semantic_scope_supplement_declaration_count',
    ),
  });
}

function stagedSemanticScopeText(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new CodeCompassGraphStagedPageContractError(
      `graph_staged_semantic_scope_${field}_invalid`,
    );
  }
  return value.trim();
}

function stagedPageCount(value: unknown, field: string): number {
  if (
    typeof value !== 'number'
    || !Number.isSafeInteger(value)
    || value < 0
  ) {
    throw new CodeCompassGraphStagedPageContractError(`graph_staged_${field}_invalid`);
  }
  return value;
}

@Injectable({ providedIn: 'root' })
export class InternalsService {
  private readonly http = inject(HttpClient);
  private readonly dir = inject(AgentDirectoryService);
  private readonly sourceControlApi = inject(SourceControlV1ApiClient);

  private hubUrl(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    return hub?.url ?? 'http://127.0.0.1:5000';
  }

  getTemplates(): Observable<AnantaTemplate[]> {
    return this.http.get<any>(`${this.hubUrl()}/templates`).pipe(
      map(resp => {
        const raw: any[] = Array.isArray(resp) ? resp : (resp.data ?? []);
        return raw.map(t => this.normalizeTemplate(t));
      }),
      catchError(() => of([])),
    );
  }

  getWorkers(): Observable<AnantaWorker[]> {
    return this.http.get<any>(`${this.hubUrl()}/api/workers`).pipe(
      map(resp => {
        const raw: any[] = resp?.data?.items ?? resp?.items ?? (Array.isArray(resp) ? resp : []);
        return raw.map(w => ({
          url: w.url ?? '',
          name: w.id ?? w.name ?? 'unknown',
          role: w.role ?? 'worker',
          status: this.mapHealth(w.health ?? w.status),
          worker_roles: Array.isArray(w.worker_roles) ? w.worker_roles : [],
          capabilities: Array.isArray(w.capabilities) ? w.capabilities : [],
        } satisfies AnantaWorker));
      }),
      catchError(() => of([])),
    );
  }

  getVpPresets(): Observable<VpPreset[]> {
    return this.http.get<any[]>(`${this.hubUrl()}/api/visual-process/presets`).pipe(
      map(resp => Array.isArray(resp) ? resp : []),
      catchError(() => of([])),
    );
  }

  getVpPreset(id: string): Observable<VpGraph | null> {
    return this.http.get<VpGraph>(`${this.hubUrl()}/api/visual-process/presets/${encodeURIComponent(id)}`).pipe(
      catchError(() => of(null)),
    );
  }

  getVpSkillProfiles(): Observable<VpSkillProfile[]> {
    return this.http.get<any[]>(`${this.hubUrl()}/api/visual-process/skill-profiles`).pipe(
      map(resp => Array.isArray(resp) ? resp : []),
      catchError(() => of([])),
    );
  }

  runDetStep(subtype: string, command: string, expectedResult: string, timeoutSec = 10): Observable<Record<string, unknown>> {
    return this.http.post<Record<string, unknown>>(`${this.hubUrl()}/api/deterministic/run`, {
      subtype, command, expected_result: expectedResult, timeout: timeoutSec,
    }).pipe(
      catchError(err => of({ success: false, error: err?.message ?? 'network error', stdout: '', stderr: '' })),
    );
  }

  dryRunVpGraph(graph: VpGraph): Observable<VpDryRunResult> {
    return this.http.post<VpDryRunResult>(`${this.hubUrl()}/api/visual-process/dry-run`, { graph }).pipe(
      catchError(err => {
        const body = err?.error;
        return of(body as VpDryRunResult);
      }),
    );
  }

  startVpWorkflow(graph: VpGraph, opts: Record<string, string> = {}): Observable<Record<string, unknown>> {
    return this.http.post<Record<string, unknown>>(`${this.hubUrl()}/api/visual-process/workflow/start`, { graph, ...opts }).pipe(
      catchError(err => of({ status: 'error', detail: err?.message ?? 'unknown' })),
    );
  }

  getVpWorkflowStatus(workflowId: string): Observable<Record<string, unknown>> {
    return this.http.get<Record<string, unknown>>(`${this.hubUrl()}/api/visual-process/workflow/${encodeURIComponent(workflowId)}/status`).pipe(
      catchError(() => of({ status: 'not_found' })),
    );
  }

  getVpWorkflowEvents(workflowId: string): Observable<Record<string, unknown>[]> {
    return this.http.get<{ events: Record<string, unknown>[] }>(`${this.hubUrl()}/api/visual-process/workflow/${encodeURIComponent(workflowId)}/events`).pipe(
      map(resp => Array.isArray(resp?.events) ? resp.events : []),
      catchError(() => of([])),
    );
  }

  getAutopilotStatus(): Observable<AutopilotStatus> {
    return this.http.get<any>(`${this.hubUrl()}/tasks/autopilot/status`).pipe(
      map(resp => resp?.data ?? resp),
      catchError(() => of(this.emptyStatus())),
    );
  }

  listKnowledgeIndexes(): Observable<readonly Record<string, unknown>[]> {
    return this.sourceControlApi.listConnections({ limit: 200 }).pipe(
      map(page => page.items.flatMap(projection => {
        const index = projection.active_index ?? projection.index;
        const knowledgeIndexId =
          typeof index?.['knowledge_index_id'] === 'string'
            ? index['knowledge_index_id']
            : '';
        if (!knowledgeIndexId) return [];
        return [{
          id: projection.connection_id,
          knowledge_index_id: knowledgeIndexId,
          source_scope: 'connection',
          status:
            typeof index?.['status'] === 'string'
              ? index['status']
              : 'active',
          index_metadata: {
            source_id: projection.connection_id,
            display_name:
              typeof projection.connection['display_name'] === 'string'
                ? projection.connection['display_name']
                : projection.connection_id,
          },
        }];
      })),
    );
  }

  getCodeCompassGraph(
    connectionId: string,
    request: CodeCompassGraphWindowRequest = { limit: 100, maxEdges: 400 },
  ): Observable<CodeCompassGraphV1> {
    return this.sourceControlApi.loadGraph(connectionId, {
      limit: request.limit,
      view: 'topology',
      maxEdges: request.maxEdges,
      ...(request.domainScope ? { domainScope: request.domainScope } : {}),
      ...(request.includeSubdomains !== undefined
        ? { includeSubdomains: request.includeSubdomains }
        : {}),
    }).pipe(
      map(graph => graph as unknown as CodeCompassGraphV1),
    );
  }

  getCodeCompassGraphInventory(
    connectionId: string,
    cursor?: string,
    limit = 250,
  ): Observable<CodeCompassGraphInventoryPage> {
    return this.sourceControlApi.loadGraph(connectionId, {
      ...(cursor ? { cursor } : {}),
      limit,
      view: 'inventory',
    }).pipe(
      map(parseCodeCompassGraphInventoryPage),
    );
  }

  getCodeCompassGraphStagedPage(
    connectionId: string,
    request: CodeCompassGraphStagedPageRequest,
  ): Observable<CodeCompassGraphStagedPage> {
    return this.sourceControlApi.loadGraph(connectionId, {
      view: 'staged',
      stage: request.stage,
      ...(request.cursor ? { cursor: request.cursor } : {}),
      limit: request.stage === 'nodes' ? request.pageSize : 500,
      maxEdges: request.stage === 'edges' ? request.pageSize : 2_000,
      ...(request.domainScope ? { domainScope: request.domainScope } : {}),
      ...(request.includeSubdomains !== undefined
        ? { includeSubdomains: request.includeSubdomains }
        : {}),
    }).pipe(
      map(page => parseCodeCompassGraphStagedPage(page, request.stage)),
    );
  }

  getWikiGraphStatus(indexId: string): Observable<any> {
    return this.http.get<any>(`${this.hubUrl()}/api/wiki-graph/status?index_id=${encodeURIComponent(indexId)}`).pipe(
      map(r => r?.data ?? null),
      catchError(() => of(null)),
    );
  }

  triggerWikiGraphBuild(indexId: string, force = false): Observable<any> {
    return this.http.post<any>(`${this.hubUrl()}/api/wiki-graph/build`, { index_id: indexId, force }).pipe(
      map(r => r?.data ?? null),
      catchError(() => of(null)),
    );
  }

  searchWikiArticles(indexId: string, query: string, limit = 20): Observable<{slug: string; title: string}[]> {
    const params = new URLSearchParams({ index_id: indexId, q: query, limit: String(limit) });
    return this.http.get<any>(`${this.hubUrl()}/api/wiki-graph/search?${params}`).pipe(
      map(r => Array.isArray(r?.data?.results) ? r.data.results : []),
      catchError(() => of([])),
    );
  }

  expandWikiArticle(indexId: string, slug: string, maxNeighbors = 40): Observable<any> {
    const params = new URLSearchParams({ index_id: indexId, slug, max_neighbors: String(maxNeighbors) });
    return this.http.get<any>(`${this.hubUrl()}/api/wiki-graph/expand?${params}`).pipe(
      map(r => r?.data ?? null),
      catchError(() => of(null)),
    );
  }

  getWikiDomainStatus(indexId: string): Observable<any> {
    return this.http.get<any>(`${this.hubUrl()}/api/wiki-graph/domain-status?index_id=${encodeURIComponent(indexId)}`).pipe(
      map(r => r?.data ?? null),
      catchError(() => of(null)),
    );
  }

  buildWikiDomains(indexId: string, mode: string, corpusPath?: string): Observable<any> {
    const body: any = { index_id: indexId, mode };
    if (corpusPath) body['corpus_path'] = corpusPath;
    return this.http.post<any>(`${this.hubUrl()}/api/wiki-graph/build-domains`, body).pipe(
      map(r => r?.data ?? null),
      catchError(() => of(null)),
    );
  }

  getWikiDomains(indexId: string, mode: string, limit = 100): Observable<any[]> {
    const params = new URLSearchParams({ index_id: indexId, mode, limit: String(limit) });
    return this.http.get<any>(`${this.hubUrl()}/api/wiki-graph/domains?${params}`).pipe(
      map(r => Array.isArray(r?.data?.domains) ? r.data.domains : []),
      catchError(() => of([])),
    );
  }

  getWikiContentStatus(indexId: string): Observable<any> {
    return this.http.get<any>(`${this.hubUrl()}/api/wiki-graph/content-status?index_id=${encodeURIComponent(indexId)}`).pipe(
      map(r => r?.data ?? null),
      catchError(() => of(null)),
    );
  }

  buildWikiContent(indexId: string, force = false): Observable<any> {
    return this.http.post<any>(`${this.hubUrl()}/api/wiki-graph/build-content`, { index_id: indexId, force }).pipe(
      map(r => r?.data ?? null),
      catchError(() => of(null)),
    );
  }

  getWikiArticleContent(indexId: string, slug: string): Observable<any> {
    const params = new URLSearchParams({ index_id: indexId, slug });
    return this.http.get<any>(`${this.hubUrl()}/api/wiki-graph/article-content?${params}`).pipe(
      map(r => r?.data ?? null),
      catchError(() => of(null)),
    );
  }

  getWikiDomainGraph(indexId: string, mode: string, domain: string, limit = 100): Observable<any> {
    const params = new URLSearchParams({ index_id: indexId, mode, domain, limit: String(limit) });
    return this.http.get<any>(`${this.hubUrl()}/api/wiki-graph/domain-graph?${params}`).pipe(
      map(r => r?.data ?? null),
      catchError(() => of(null)),
    );
  }

  getWikiDomainArticles(indexId: string, mode: string, domain: string, limit = 50): Observable<any[]> {
    const params = new URLSearchParams({ index_id: indexId, mode, domain, limit: String(limit) });
    return this.http.get<any>(`${this.hubUrl()}/api/wiki-graph/domain-articles?${params}`).pipe(
      map(r => Array.isArray(r?.data?.articles) ? r.data.articles : []),
      catchError(() => of([])),
    );
  }

  private normalizeTemplate(t: any): AnantaTemplate {
    const name: string = t.name ?? '';
    let category: AnantaTemplate['category'] = 'system';
    if (name.toLowerCase().includes('scrum') && !name.toLowerCase().includes('opencode')) category = 'scrum';
    else if (name.toLowerCase().includes('opencode')) category = 'opencode';
    else if (name.toLowerCase().includes('kanban')) category = 'kanban';
    return {
      id: t.id ?? '',
      name,
      description: t.description ?? '',
      category,
      prompt_template: t.prompt_template ?? '',
      is_seed: Boolean(t.is_seed),
    };
  }

  private mapHealth(h: string): AnantaWorker['status'] {
    const l = (h ?? '').toLowerCase();
    if (l === 'online' || l === 'healthy') return 'online';
    if (l === 'degraded') return 'degraded';
    return 'offline';
  }

  private emptyStatus(): AutopilotStatus {
    return {
      running: false, goal: '', team_id: '', started_at: null,
      tick_count: 0, dispatched_count: 0, completed_count: 0, failed_count: 0,
      last_error: null,
      effective_security_policy: { level: 'safe', max_concurrency_cap: 1, allowed_tool_classes: [] },
      circuit_breakers: { open_workers: [], open_count: 0, failure_streak: {} },
    };
  }
}
