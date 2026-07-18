import { Injectable } from '@angular/core';

import {
  CanvasHitTarget,
  VpAssistantDetailLevel,
  VpAssistantLocation,
  VpEditorContextEnvelope,
  VpEditorContextPayload,
} from './vp-editor-context.models';
import { ValidationIssue, VpGraph, VpRuntimeOverlay } from './visual-process-api.service';
import { VP_NODE_REGISTRY_VERSION } from './vp-node-definition-registry.service';

const EDITOR_CONTEXT_VERSION = 'ananta.visual_process.editor_context.v1' as const;
const VISUAL_PROCESS_PROMPT_VERSION = 'visual-process-assistant.v1';
const MAX_EDITOR_CONTEXT_BYTES = 256 * 1024;
const MAX_SAFE_INTEGER = 9_007_199_254_740_991;
const VOLATILE_KEYS = new Set([
  'created_at', 'updated_at', 'started_at', 'finished_at',
  'duration_ms', 'duration_seconds', 'poll_interval_ms', 'animation_frame',
  'dom_id', 'client_x', 'client_y', 'screen_x', 'screen_y',
]);
const INLINE_SECRET_KEYS = new Set([
  'api_key', 'apikey', 'access_token', 'refresh_token', 'password',
  'credential', 'client_secret', 'private_key',
]);

type CanonicalJsonValue = null | boolean | number | string | CanonicalJsonValue[] | { [key: string]: CanonicalJsonValue };

function compareCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, char => char.codePointAt(0)!);
  const rightPoints = Array.from(right, char => char.codePointAt(0)!);
  for (let index = 0; index < Math.min(leftPoints.length, rightPoints.length); index++) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
  }
  return leftPoints.length - rightPoints.length;
}

function compareTuple(left: readonly string[], right: readonly string[]): number {
  for (let index = 0; index < Math.min(left.length, right.length); index++) {
    const comparison = compareCodePoints(left[index], right[index]);
    if (comparison) return comparison;
  }
  return left.length - right.length;
}

function pythonSortString(value: CanonicalJsonValue | undefined): string {
  if (value === undefined) return '';
  if (value === null) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  return String(value);
}

function canonicalContextValue(value: unknown, parentKey = ''): CanonicalJsonValue {
  if (value === null) return null;
  if (typeof value === 'boolean' || typeof value === 'number') return value;
  if (typeof value === 'string') return value.normalize('NFC');
  if (Array.isArray(value)) {
    const items = value.map(item => canonicalContextValue(item, parentKey));
    const sortKeys: Record<string, (item: Record<string, CanonicalJsonValue>) => string[]> = {
      steps: item => [pythonSortString(item['id'])],
      edges: item => [pythonSortString(item['id']), pythonSortString(item['source']), pythonSortString(item['target'])],
      validation_issues: item => [pythonSortString(item['path']), pythonSortString(item['code']), pythonSortString(item['message'])],
      evidence_refs: item => [pythonSortString(item['source_id']), pythonSortString(item['source_version']), pythonSortString(item['evidence_id'])],
    };
    const sorter = sortKeys[parentKey];
    if (sorter && items.every(item => item !== null && !Array.isArray(item) && typeof item === 'object')) {
      items.sort((left, right) => compareTuple(
        sorter(left as Record<string, CanonicalJsonValue>),
        sorter(right as Record<string, CanonicalJsonValue>),
      ));
    }
    return items;
  }
  if (typeof value !== 'object' || value === undefined) throw new Error('canonical_context_value_unsupported');

  const result: Record<string, CanonicalJsonValue> = {};
  for (const rawKey of Object.keys(value as Record<string, unknown>)) {
    const key = rawKey.normalize('NFC');
    if (VOLATILE_KEYS.has(key)) continue;
    if (Object.prototype.hasOwnProperty.call(result, key)) {
      throw new Error('canonical_context_duplicate_normalized_key');
    }
    result[key] = canonicalContextValue((value as Record<string, unknown>)[rawKey], key);
  }
  return result;
}

function fixedDecimal(value: number): string {
  const raw = value.toString().toLowerCase();
  if (!raw.includes('e')) return raw;
  const [coefficient, exponentText] = raw.split('e');
  const exponent = Number(exponentText);
  const negative = coefficient.startsWith('-');
  const digits = coefficient.replace('-', '').replace('.', '');
  const decimalIndex = coefficient.replace('-', '').indexOf('.') < 0
    ? coefficient.replace('-', '').length + exponent
    : coefficient.replace('-', '').indexOf('.') + exponent;
  let rendered: string;
  if (decimalIndex <= 0) rendered = `0.${'0'.repeat(-decimalIndex)}${digits}`;
  else if (decimalIndex >= digits.length) rendered = `${digits}${'0'.repeat(decimalIndex - digits.length)}`;
  else rendered = `${digits.slice(0, decimalIndex)}.${digits.slice(decimalIndex)}`;
  return `${negative ? '-' : ''}${rendered}`;
}

function canonicalNumber(value: number): string {
  if (!Number.isFinite(value)) throw new Error('canonical_context_number_non_finite');
  if (Object.is(value, -0) || value === 0) return '0';
  if (Number.isInteger(value)) {
    if (!Number.isSafeInteger(value) || Math.abs(value) > MAX_SAFE_INTEGER) {
      throw new Error('canonical_context_integer_out_of_range');
    }
    return String(value);
  }
  if (Math.abs(value) > 1_000_000_000_000 || Math.abs(value) < 0.000_000_001) {
    throw new Error('canonical_context_float_out_of_range');
  }
  const rendered = fixedDecimal(value);
  const fractional = rendered.split('.')[1] ?? '';
  if (fractional.length > 12) throw new Error('canonical_context_float_precision_exceeded');
  return rendered.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') || '0';
}

function canonicalJson(value: CanonicalJsonValue): string {
  if (value === null) return 'null';
  if (value === true) return 'true';
  if (value === false) return 'false';
  if (typeof value === 'number') return canonicalNumber(value);
  if (typeof value === 'string') return JSON.stringify(value.normalize('NFC'));
  if (Array.isArray(value)) return `[${value.map(item => canonicalJson(item)).join(',')}]`;
  const keys = Object.keys(value).sort(compareCodePoints);
  return `{${keys.map(key => `${canonicalJson(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

export function canonicalVpJson(value: unknown): string {
  const payload = canonicalJson(canonicalContextValue(value));
  if (new TextEncoder().encode(payload).byteLength > MAX_EDITOR_CONTEXT_BYTES) {
    throw new Error('editor_context_size_limit_exceeded');
  }
  return payload;
}

export async function sha256VpCanonicalJson(value: unknown): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error('web_crypto_unavailable');
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonicalVpJson(value)));
  return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function isInlineSecretKey(key: string): boolean {
  const normalized = key.toLocaleLowerCase('en-US').replaceAll('-', '_');
  return INLINE_SECRET_KEYS.has(normalized) && !normalized.endsWith('_secret_ref');
}

function safeContextValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(item => item === undefined ? null : safeContextValue(item));
  if (value === null || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .filter(([key, item]) => item !== undefined && !isInlineSecretKey(key))
    .map(([key, item]) => [key, safeContextValue(item)]));
}

function definitionProjection(graph: VpGraph): Record<string, unknown> {
  const metadata = { ...(graph.metadata ?? {}) };
  delete metadata['source_refs'];
  delete metadata['evidence_refs'];
  return safeContextValue({
    id: graph.id || 'unsaved-graph',
    name: graph.name,
    description: graph.description,
    version: graph.version,
    graph_schema_version: graph.graph_schema_version ?? '1',
    node_registry_version: graph.node_registry_version ?? VP_NODE_REGISTRY_VERSION,
    tags: graph.tags,
    metadata,
    steps: graph.steps.map(({ run_state: _runtimeState, ...step }) => step),
    edges: graph.edges,
  }) as Record<string, unknown>;
}

function graphExcerpt(graph: VpGraph, target: CanvasHitTarget): Record<string, unknown> {
  const projection = definitionProjection(graph);
  let steps = projection['steps'] as Array<Record<string, unknown>>;
  let edges = projection['edges'] as Array<Record<string, unknown>>;
  if (target.stepId) {
    const selectedIds = new Set([target.stepId]);
    for (const edge of graph.edges) {
      if (edge.source === target.stepId || edge.target === target.stepId) {
        selectedIds.add(edge.source);
        selectedIds.add(edge.target);
      }
    }
    steps = steps.filter(step => selectedIds.has(String(step['id'] ?? '')));
    edges = edges.filter(edge => selectedIds.has(String(edge['source'] ?? '')) && selectedIds.has(String(edge['target'] ?? '')));
  } else if (target.edgeId) {
    edges = edges.filter(edge => String(edge['id'] ?? '') === target.edgeId);
    const selectedIds = new Set(edges.flatMap(edge => [String(edge['source'] ?? ''), String(edge['target'] ?? '')]));
    steps = steps.filter(step => selectedIds.has(String(step['id'] ?? '')));
  }
  return {
    graph_id: projection['id'],
    name: projection['name'],
    description: projection['description'],
    steps: steps.slice(0, 50),
    edges: edges.slice(0, 100),
    total_step_count: graph.steps.length,
    total_edge_count: graph.edges.length,
    excerpt_truncated: steps.length > 50 || edges.length > 100,
  };
}

export function canvasTargetToAssistantLocation(graphId: string, target: CanvasHitTarget): VpAssistantLocation {
  switch (target.kind) {
    case 'node': return { target_kind: 'node', graph_id: graphId, entity_id: target.stepId ?? target.entityId, role: target.role };
    case 'node_port': return {
      target_kind: 'field', graph_id: graphId, entity_id: target.stepId,
      field_path: `/io/${target.portDirection === 'output' ? 'outputs' : 'inputs'}/${target.portName ?? ''}`, role: target.role,
    };
    case 'edge': return { target_kind: 'edge', graph_id: graphId, entity_id: target.edgeId ?? target.entityId, role: target.role };
    case 'edge_condition': return { target_kind: 'edge', graph_id: graphId, entity_id: target.edgeId, field_path: '/condition', role: target.role };
    case 'validation_badge': return { target_kind: 'validation', graph_id: graphId, entity_id: target.stepId, role: target.role };
    case 'runtime_badge': return { target_kind: 'runtime', graph_id: graphId, entity_id: target.stepId, role: target.role };
    case 'palette_item': return { target_kind: 'palette_item', graph_id: graphId, entity_id: target.entityId, role: target.role };
    default: return { target_kind: 'canvas', graph_id: graphId, role: target.role };
  }
}

function effectiveConfiguration(graph: VpGraph, target: CanvasHitTarget): Record<string, unknown> {
  const step = target.stepId ? graph.steps.find(item => item.id === target.stepId) : undefined;
  const edge = target.edgeId ? graph.edges.find(item => item.id === target.edgeId) : undefined;
  const stableStep = step
    ? (({ run_state: _runtimeState, ...definition }) => definition)(step)
    : undefined;
  const graphMetadata = { ...(graph.metadata ?? {}) };
  delete graphMetadata['source_refs'];
  delete graphMetadata['evidence_refs'];
  return safeContextValue({
    graph_metadata: graphMetadata,
    ...(stableStep ? { step_kind: stableStep.kind, step: stableStep } : {}),
    ...(edge ? { edge } : {}),
  }) as Record<string, unknown>;
}

function editorMode(mode: 'embedded-edit' | 'full-editor' | 'compact-readonly'): VpEditorContextPayload['editor_mode'] {
  if (mode === 'compact-readonly') return 'read_only';
  return mode === 'embedded-edit' ? 'ai_snake' : 'editor';
}

function groundingValue(explicit: string | undefined, graph: VpGraph, metadataKey: string): string {
  const metadataValue = graph.metadata?.[metadataKey];
  const value = explicit ?? (typeof metadataValue === 'string' ? metadataValue : undefined);
  return value?.trim() || 'unverified';
}

@Injectable({ providedIn: 'root' })
export class VpAssistantContextService {
  async assemble(options: {
    graph: VpGraph;
    target: CanvasHitTarget;
    detailLevel: VpAssistantDetailLevel;
    editorMode: 'embedded-edit' | 'full-editor' | 'compact-readonly';
    runtime?: VpRuntimeOverlay | null;
    validationIssues?: readonly ValidationIssue[];
    repositoryRevision?: string;
    codecompassManifestHash?: string;
    sourceAllowlistVersion?: string;
    promptVersion?: string;
    locale?: string;
  }): Promise<VpEditorContextEnvelope> {
    const graphId = options.graph.id || 'unsaved-graph';
    const definition = definitionProjection(options.graph);
    const draftHash = await sha256VpCanonicalJson(definition);
    const runtime = options.runtime ? safeContextValue(options.runtime) as VpRuntimeOverlay : undefined;
    const payload: VpEditorContextPayload = {
      contract_version: EDITOR_CONTEXT_VERSION,
      graph_id: graphId,
      repository_revision: groundingValue(options.repositoryRevision, options.graph, 'repository_revision'),
      codecompass_manifest_hash: groundingValue(options.codecompassManifestHash, options.graph, 'codecompass_manifest_hash'),
      source_allowlist_version: groundingValue(options.sourceAllowlistVersion, options.graph, 'source_allowlist_version'),
      prompt_version: options.promptVersion?.trim() || VISUAL_PROCESS_PROMPT_VERSION,
      graph_schema_version: options.graph.graph_schema_version ?? '1',
      node_registry_version: options.graph.node_registry_version ?? VP_NODE_REGISTRY_VERSION,
      definition_revision: options.graph.definition_revision ?? 0,
      definition_hash: options.graph.base_graph_hash?.replace(/^sha256:/, '') || draftHash,
      draft_hash: draftHash,
      ...(runtime ? { runtime_snapshot_hash: await sha256VpCanonicalJson(runtime) } : {}),
      editor_mode: editorMode(options.editorMode),
      locale: options.locale || 'de',
      location: canvasTargetToAssistantLocation(graphId, options.target),
      graph_excerpt: graphExcerpt(options.graph, options.target),
      effective_configuration: effectiveConfiguration(options.graph, options.target),
      validation_issues: safeContextValue(options.validationIssues ?? []) as Array<Record<string, unknown>>,
      ...(runtime ? { runtime_overlay: runtime } : {}),
      evidence_refs: [],
      allowed_mutations: options.editorMode === 'compact-readonly' ? [] : ['update_step_field'],
      extensions: {},
    };
    return {
      ...payload,
      context_id: `ctx-sha256:${await sha256VpCanonicalJson(payload)}`,
      detail_level: options.detailLevel,
    };
  }
}
