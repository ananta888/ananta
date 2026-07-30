export const SOURCE_DESCRIPTOR_CONTRACT = 'source_descriptor.v1' as const;
export const SOURCE_SNAPSHOT_CONTRACT = 'source_snapshot.v1' as const;
export const SOURCE_PACK_CONTRACT = 'source_pack.v1' as const;
export const KNOWLEDGE_INDEX_CONTRACT = 'knowledge_index.v1' as const;
export const CODECOMPASS_GRAPH_CONTRACT = 'codecompass_graph.v1' as const;
export const CODECOMPASS_QUERY_CONTRACT = 'codecompass_query.v1' as const;

export type SourceControlViewState =
  | 'loading'
  | 'empty'
  | 'ready'
  | 'offline'
  | 'unauthorized'
  | 'forbidden'
  | 'not-found'
  | 'conflict'
  | 'unprocessable'
  | 'rate-limited'
  | 'server-error'
  | 'error';

export type SourceControlErrorKind = Exclude<
  SourceControlViewState,
  'loading' | 'empty' | 'ready'
>;

export class SourceControlApiError extends Error {
  constructor(
    readonly kind: SourceControlErrorKind,
    readonly status: number,
    readonly reasonCode: string,
    message: string,
    readonly cause?: unknown,
  ) {
    super(message);
    this.name = 'SourceControlApiError';
  }
}

export interface SourceSnapshotV1 {
  readonly schema: typeof SOURCE_SNAPSHOT_CONTRACT;
  readonly snapshot_id: string;
  readonly status: string;
  readonly retrieved_at?: string;
  readonly content_hash?: string;
  readonly reason_code?: string;
  readonly human_message?: string;
  readonly extensions: Readonly<Record<string, unknown>>;
}

export interface SourceDescriptorV1 {
  readonly schema: typeof SOURCE_DESCRIPTOR_CONTRACT;
  readonly source_id: string;
  readonly source_type: string;
  readonly display_name: string;
  readonly trust_level: string;
  readonly enabled: boolean;
  readonly fetch_source: Readonly<Record<string, unknown>>;
  readonly citation_source: Readonly<Record<string, unknown>>;
  readonly latest_snapshot: SourceSnapshotV1 | null;
  readonly extensions: Readonly<Record<string, unknown>>;
}

export interface SourcePackV1 {
  readonly schema: typeof SOURCE_PACK_CONTRACT;
  readonly source_pack_id: string;
  readonly display_name: string;
  readonly version: string;
  readonly sources: readonly Readonly<{ source_id: string }>[];
}

export interface KnowledgeIndexV1 {
  readonly schema: typeof KNOWLEDGE_INDEX_CONTRACT;
  readonly knowledge_index_id: string;
  readonly artifact_id: string | null;
  readonly collection_id: string | null;
  readonly latest_run_id: string | null;
  readonly source_scope: string;
  readonly profile_name: string;
  readonly status: string;
  readonly index_metadata: Readonly<Record<string, unknown>>;
  readonly created_at: number | null;
  readonly updated_at: number | null;
}

export interface CodeCompassGraphV1 {
  readonly schema: string;
  readonly knowledge_index_id: string;
  readonly nodes: readonly Readonly<Record<string, unknown>>[];
  readonly edges: readonly Readonly<Record<string, unknown>>[];
  readonly metadata: Readonly<Record<string, unknown>>;
  readonly diagnostics: Readonly<Record<string, unknown>>;
  readonly warnings: readonly string[];
}

export interface CodeCompassQueryV1 {
  readonly schema: typeof CODECOMPASS_QUERY_CONTRACT;
  readonly knowledge_index_id: string;
  readonly query_type: string;
  readonly seed: string;
  readonly field?: string;
  readonly depth?: number;
  readonly direction?: 'incoming' | 'outgoing' | 'both';
}

export interface CodeCompassQueryResultV1 {
  readonly schema: string;
  readonly knowledge_index_id: string;
  readonly payload: Readonly<Record<string, unknown>>;
}

export interface CodeCompassLifecycleCapabilitiesV1 {
  readonly schema: 'codecompass_lifecycle_capabilities.v1';
  readonly reindex: false;
  readonly activate: false;
  readonly rollback: false;
  readonly cleanup: false;
  readonly reason_code: 'codecompass_lifecycle_routes_unavailable';
}

export function normalizeSourceList(value: unknown): readonly SourceDescriptorV1[] {
  if (!Array.isArray(value)) {
    throw contractError('sources_response_invalid');
  }
  return value.map(normalizeSource);
}

export function normalizeSource(value: unknown): SourceDescriptorV1 {
  const raw = requireRecord(value, 'source_invalid');
  return {
    schema: SOURCE_DESCRIPTOR_CONTRACT,
    source_id: requireId(raw['source_id'], 'source_id_invalid'),
    source_type: requireText(raw['source_type'], 'source_type_invalid', 128),
    display_name: requireText(raw['display_name'], 'source_display_name_invalid', 256),
    trust_level: text(raw['trust_level'], 128),
    enabled: raw['enabled'] !== false,
    fetch_source: record(raw['fetch_source']),
    citation_source: record(raw['citation_source']),
    latest_snapshot: raw['latest_snapshot']
      ? normalizeSnapshot(raw['latest_snapshot'])
      : null,
    extensions: record(raw['extensions']),
  };
}

export function normalizeSnapshotList(value: unknown): readonly SourceSnapshotV1[] {
  if (!Array.isArray(value)) {
    throw contractError('source_snapshots_response_invalid');
  }
  return value.map(normalizeSnapshot);
}

export function normalizeSnapshot(value: unknown): SourceSnapshotV1 {
  const raw = requireRecord(value, 'source_snapshot_invalid');
  return {
    schema: SOURCE_SNAPSHOT_CONTRACT,
    snapshot_id: requireId(raw['snapshot_id'], 'snapshot_id_invalid'),
    status: requireText(raw['status'], 'snapshot_status_invalid', 128),
    retrieved_at: optionalText(raw['retrieved_at'], 128),
    content_hash: optionalText(raw['content_hash'], 128),
    reason_code: optionalText(raw['reason_code'], 128),
    human_message: optionalText(raw['human_message'], 512),
    extensions: record(raw['extensions']),
  };
}

export function normalizeSourcePackList(value: unknown): readonly SourcePackV1[] {
  if (!Array.isArray(value)) {
    throw contractError('source_packs_response_invalid');
  }
  return value.map(item => {
    const raw = requireRecord(item, 'source_pack_invalid');
    const sources = Array.isArray(raw['sources'])
      ? raw['sources'].map(source => ({
          source_id: requireId(
            requireRecord(source, 'source_pack_entry_invalid')['source_id'],
            'source_pack_source_id_invalid',
          ),
        }))
      : [];
    return {
      schema: SOURCE_PACK_CONTRACT,
      source_pack_id: requireId(raw['source_pack_id'], 'source_pack_id_invalid'),
      display_name: requireText(raw['display_name'], 'source_pack_display_name_invalid', 256),
      version: requireText(raw['version'], 'source_pack_version_invalid', 128),
      sources,
    };
  });
}

export function normalizeKnowledgeIndexList(value: unknown): readonly KnowledgeIndexV1[] {
  const raw = record(value);
  const items = Array.isArray(value)
    ? value
    : Array.isArray(raw['items'])
      ? raw['items']
      : null;
  if (!items) {
    throw contractError('knowledge_indices_response_invalid');
  }
  return items.map(normalizeKnowledgeIndex);
}

export function normalizeKnowledgeIndex(value: unknown): KnowledgeIndexV1 {
  const raw = requireRecord(value, 'knowledge_index_invalid');
  return {
    schema: KNOWLEDGE_INDEX_CONTRACT,
    knowledge_index_id: requireId(
      raw['knowledge_index_id'] ?? raw['id'],
      'knowledge_index_id_invalid',
    ),
    artifact_id: optionalId(raw['artifact_id']),
    collection_id: optionalId(raw['collection_id']),
    latest_run_id: optionalId(raw['latest_run_id']),
    source_scope: requireText(raw['source_scope'], 'knowledge_index_source_scope_invalid', 128),
    profile_name: text(raw['profile_name'], 128) || 'default',
    status: requireText(raw['status'], 'knowledge_index_status_invalid', 128),
    index_metadata: record(raw['index_metadata']),
    created_at: optionalNumber(raw['created_at']),
    updated_at: optionalNumber(raw['updated_at']),
  };
}

export function normalizeCodeCompassGraph(
  value: unknown,
  knowledgeIndexId: string,
): CodeCompassGraphV1 {
  const raw = requireRecord(value, 'codecompass_graph_response_invalid');
  const metadata = record(raw['metadata']);
  return {
    schema: text(raw['schema'], 128) || CODECOMPASS_GRAPH_CONTRACT,
    knowledge_index_id: requireId(
      metadata['knowledge_index_id'] ?? knowledgeIndexId,
      'knowledge_index_id_invalid',
    ),
    nodes: recordArray(raw['nodes']),
    edges: recordArray(raw['edges']),
    metadata,
    diagnostics: record(raw['diagnostics']),
    warnings: stringArray(raw['warnings'], 100),
  };
}

export function normalizeCodeCompassQueryResult(
  value: unknown,
  knowledgeIndexId: string,
): CodeCompassQueryResultV1 {
  const payload = requireRecord(value, 'codecompass_query_response_invalid');
  const metadata = record(payload['metadata']);
  return {
    schema: text(payload['schema'], 128) || 'codecompass_query_result.v1',
    knowledge_index_id: requireId(
      metadata['knowledge_index_id'] ?? knowledgeIndexId,
      'knowledge_index_id_invalid',
    ),
    payload,
  };
}

export function normalizeContractId(value: unknown, reasonCode = 'entity_id_invalid'): string {
  return requireId(value, reasonCode);
}

export function toSourceControlApiError(
  error: unknown,
  operation: string,
): SourceControlApiError {
  if (error instanceof SourceControlApiError) return error;
  const raw = record(error);
  const body = record(raw['error']);
  const envelope = record(body['data']);
  const status = Number(raw['status'] ?? body['status'] ?? envelope['status'] ?? 0);
  const kind = status === 401
    ? 'unauthorized'
    : status === 403
      ? 'forbidden'
      : status === 404
        ? 'not-found'
        : status === 409
          ? 'conflict'
          : status === 422
            ? 'unprocessable'
            : status === 429
              ? 'rate-limited'
              : status >= 500
                ? 'server-error'
                : status === 0
                  ? 'offline'
                  : 'error';
  const reasonCode = text(
    envelope['reason_code'] ?? body['reason_code'] ?? `${operation}_failed`,
    128,
  ) || `${operation}_failed`;
  return new SourceControlApiError(
    kind,
    Number.isFinite(status) ? status : 0,
    reasonCode,
    safeErrorMessage(kind, operation),
    error,
  );
}

function safeErrorMessage(kind: SourceControlErrorKind, operation: string): string {
  const messages: Record<SourceControlErrorKind, string> = {
    offline: 'Der Hub ist nicht erreichbar.',
    unauthorized: 'Die Anmeldung ist abgelaufen oder fehlt.',
    forbidden: 'Für diese Aktion fehlt die Berechtigung.',
    'not-found': 'Die angeforderte Ressource wurde nicht gefunden.',
    conflict: 'Der Stand wurde zwischenzeitlich geändert.',
    unprocessable: 'Die Anfrage entspricht nicht dem erwarteten Vertrag.',
    'rate-limited': 'Zu viele Anfragen. Bitte später erneut versuchen.',
    'server-error': 'Der Hub konnte die Anfrage nicht verarbeiten.',
    error: `${operation} ist fehlgeschlagen.`,
  };
  return messages[kind];
}

function contractError(reasonCode: string): SourceControlApiError {
  return new SourceControlApiError(
    'unprocessable',
    422,
    reasonCode,
    'Die Hub-Antwort entspricht nicht dem erwarteten Vertrag.',
  );
}

function requireRecord(value: unknown, reasonCode: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw contractError(reasonCode);
  }
  return value as Record<string, unknown>;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function requireId(value: unknown, reasonCode: string): string {
  const candidate = text(value, 192);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,191}$/.test(candidate)) {
    throw contractError(reasonCode);
  }
  return candidate;
}

function optionalId(value: unknown): string | null {
  if (value === undefined || value === null || value === '') return null;
  return requireId(value, 'related_entity_id_invalid');
}

function requireText(value: unknown, reasonCode: string, limit: number): string {
  const candidate = text(value, limit);
  if (!candidate) throw contractError(reasonCode);
  return candidate;
}

function optionalText(value: unknown, limit: number): string | undefined {
  const candidate = text(value, limit);
  return candidate || undefined;
}

function text(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

function optionalNumber(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function recordArray(value: unknown): readonly Readonly<Record<string, unknown>>[] {
  return Array.isArray(value)
    ? value.map(item => requireRecord(item, 'codecompass_graph_entry_invalid'))
    : [];
}

function stringArray(value: unknown, limit: number): readonly string[] {
  return Array.isArray(value)
    ? value.slice(0, limit).map(item => text(item, 512)).filter(Boolean)
    : [];
}
