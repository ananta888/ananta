import { TaskKindInfo, VpGraph, VpStep } from './visual-process-api.service';
import { GENERATED_VISUAL_PROCESS_TASK_KINDS } from './vp-node-definitions.generated';

export const NODE_W = 140;
export const NODE_H = 52;

/** Offline fallback generated from the same Hub NodeDefinition registry contract. */
export const FALLBACK_KINDS: TaskKindInfo[] = GENERATED_VISUAL_PROCESS_TASK_KINDS.map(kind => ({
  ...kind,
  side_effects: [...(kind.side_effects ?? [])],
  legacy_aliases: [...(kind.legacy_aliases ?? [])],
}));

export const ENCODING_MODES = ['off', 'float32', 'float16', 'int8', 'symmetric4bit', 'turboquant_mse_experimental'];
export const RAG_CHANNELS = ['dense', 'lexical', 'symbol', 'codecompass_fts', 'codecompass_vector', 'codecompass_graph'];
export const POLL_INTERVAL_MS = 3000;
export const POLL_MAX_MS = 10 * 60 * 1000;

export function uid(): string { return Math.random().toString(36).slice(2, 10); }
export function edgeId(): string { return `edge-${uid()}`; }
export function stepId(): string { return `step-${uid()}`; }

export function emptyGraph(): VpGraph {
  return {
    id: `vp-${uid()}`,
    name: 'Neuer Prozess',
    description: '',
    version: '1.0',
    steps: [],
    edges: [],
    tags: [],
    metadata: {},
  };
}
export function hintColor(hints: string[]): string {
  if (hints.includes('high_risk') || hints.includes('mutates_production')) return '#ff6b6b';
  if (hints.includes('requires_approval')) return '#fdcb6e';
  if (hints.includes('evolution') || hints.includes('self_modifying')) return '#e84393';
  if (hints.includes('index_write')) return '#6c5ce7';
  if (hints.includes('retrieval')) return '#a29bfe';
  if (hints.includes('vector_operation') || hints.includes('quantization')) return '#00b894';
  if (hints.includes('ml_inference')) return '#00cec9';
  if (hints.includes('read_only')) return '#74b9ff';
  return '#636e72';
}

export const RETRIEVAL_KINDS = new Set([
  'codecompass_index_build', 'codecompass_vector_search',
  'codecompass_fts_search', 'codecompass_graph_expand',
]);
export const EVOLUTION_KINDS = new Set([
  'evolution_analyze', 'evolution_validate', 'evolution_apply',
  'evolve_prompt', 'evolve_project',
]);
export const WORKSPACE_KINDS = new Set(['workspace_snapshot', 'workspace_diff']);

export function nodeKindColor(kind: string): string {
  if (kind === 'fork' || kind === 'join' || kind === 'parallel') return '#00b894';
  if (kind === 'approval') return '#55efc4';
  if (RETRIEVAL_KINDS.has(kind)) return '#6c5ce7';
  if (EVOLUTION_KINDS.has(kind)) return '#e84393';
  if (WORKSPACE_KINDS.has(kind)) return '#b2bec3';
  if (kind === 'ml_intern_build_lora_dataset') return '#00b894';
  if (kind === 'ml_intern_train_lora') return '#fdcb6e';
  if (kind === 'turboquant_mse' || kind === 'sign_rotation') return '#00b894';
  if (kind === 'embed_api' || kind === 'embed_chunk') return '#00cec9';
  return '';
}

export function autoLayoutGraph(graph: VpGraph): VpGraph {
  const forwardEdges = graph.edges.filter(edge => edge.condition.kind !== 'back_edge');
  const inDegree: Record<string, number> = {};
  const adjacent: Record<string, string[]> = {};
  for (const step of graph.steps) {
    inDegree[step.id] = 0;
    adjacent[step.id] = [];
  }
  for (const edge of forwardEdges) {
    inDegree[edge.target] = (inDegree[edge.target] ?? 0) + 1;
    adjacent[edge.source].push(edge.target);
  }
  const queue = graph.steps.filter(step => !inDegree[step.id]).map(step => step.id);
  const depths: Record<string, number> = {};
  const order: string[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    order.push(id);
    for (const next of adjacent[id]) {
      inDegree[next]--;
      depths[next] = Math.max(depths[next] ?? 0, (depths[id] ?? 0) + 1);
      if (inDegree[next] === 0) queue.push(next);
    }
  }
  for (const step of graph.steps) if (!order.includes(step.id)) order.push(step.id);
  const rowsByColumn: Record<number, string[]> = {};
  for (const id of order) (rowsByColumn[depths[id] ?? 0] ??= []).push(id);
  const positions: Record<string, { x: number; y: number }> = {};
  for (const [column, ids] of Object.entries(rowsByColumn)) {
    ids.forEach((id, row) => positions[id] = {
      x: 40 + Number(column) * (NODE_W + 60),
      y: 40 + row * (NODE_H + 40),
    });
  }
  return {
    ...graph,
    steps: graph.steps.map(step => ({ ...step, position: positions[step.id] ?? step.position })),
  };
}
