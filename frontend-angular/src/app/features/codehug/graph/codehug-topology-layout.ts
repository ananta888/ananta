import {
  ChTopologyReadModel,
  ChHubInstanceReadModel,
  ChWorkerInstanceReadModel,
  ChTestLayerReadModel,
  ChRoutingRuleReadModel,
} from '../models/codehug.models';

// ─────────────────────────────────────────────────────────────────────────────
// Canvas data models
// ─────────────────────────────────────────────────────────────────────────────

export type ChNodeKind =
  | 'hub'
  | 'worker-llm'
  | 'worker-det'
  | 'policy-layer'
  | 'test-layer'
  | 'routing-rule';

export type ChNodeRunState = 'idle' | 'active' | 'completed' | 'failed' | 'skipped';

export interface ChCanvasNode {
  id: string;
  kind: ChNodeKind;
  label: string;
  sublabel: string;
  badge?: string;
  x: number;
  y: number;
  w: number;
  h: number;
  runState: ChNodeRunState;
  payload: ChHubInstanceReadModel | ChWorkerInstanceReadModel | ChTestLayerReadModel | ChRoutingRuleReadModel;
}

export interface ChCanvasEdge {
  id: string;
  fromId: string;
  toId: string;
  label?: string;
  runState: ChNodeRunState;
}

// ─────────────────────────────────────────────────────────────────────────────
// Auto-layout
// ─────────────────────────────────────────────────────────────────────────────

const NODE_W_HUB = 220;
const NODE_H_HUB = 56;
const NODE_W_WORKER = 160;
const NODE_H_WORKER = 62;
const NODE_W_LAYER = 170;
const NODE_H_LAYER = 58;
const NODE_W_RULE = 170;
const NODE_H_RULE = 50;

export function buildTopologyGraph(topology: ChTopologyReadModel): { nodes: ChCanvasNode[]; edges: ChCanvasEdge[] } {
  const nodes: ChCanvasNode[] = [];
  const edges: ChCanvasEdge[] = [];

  const canvasW = 900;
  const hubY = 40;
  const workerLlmY = 180;
  const workerDetY = 310;
  const layerY = 60;
  const ruleY = 220;

  // Hubs — centered at top
  const hubTotal = topology.hubs.length * (NODE_W_HUB + 40) - 40;
  topology.hubs.forEach((h, i) => {
    const x = (canvasW - hubTotal) / 2 + i * (NODE_W_HUB + 40);
    nodes.push({
      id: `hub::${h.id}`,
      kind: 'hub',
      label: `Hub · ${h.id.slice(0, 14)}`,
      sublabel: h.url,
      badge: h.status,
      x,
      y: hubY,
      w: NODE_W_HUB,
      h: NODE_H_HUB,
      runState: 'idle',
      payload: h,
    });
  });

  // Workers — LLM below hub, Det below LLM
  const llmWorkers = topology.workers.filter(w => w.cliBackend !== 'deterministic');
  const detWorkers = topology.workers.filter(w => w.cliBackend === 'deterministic');

  const llmGap = llmWorkers.length > 0 ? Math.max(180, canvasW / (llmWorkers.length + 1)) : 180;
  llmWorkers.forEach((w, i) => {
    const x = 40 + i * llmGap;
    const nid = `worker::${w.id}`;
    nodes.push({
      id: nid,
      kind: 'worker-llm',
      label: w.cliBackend,
      sublabel: w.model.length > 18 ? w.model.slice(0, 16) + '…' : w.model,
      badge: w.llmProvider,
      x,
      y: workerLlmY,
      w: NODE_W_WORKER,
      h: NODE_H_WORKER,
      runState: 'idle',
      payload: w,
    });
    // connect each hub to each llm worker
    topology.hubs.forEach(h => {
      edges.push({ id: `e-${h.id}-${w.id}`, fromId: `hub::${h.id}`, toId: nid, runState: 'idle' });
    });
  });

  const detGap = detWorkers.length > 0 ? Math.max(180, canvasW / (detWorkers.length + 1)) : 180;
  detWorkers.forEach((w, i) => {
    const x = 40 + i * detGap;
    const nid = `worker::${w.id}`;
    nodes.push({
      id: nid,
      kind: 'worker-det',
      label: 'deterministic',
      sublabel: w.type,
      x,
      y: workerDetY,
      w: NODE_W_WORKER,
      h: NODE_H_WORKER,
      runState: 'idle',
      payload: w,
    });
    topology.hubs.forEach(h => {
      edges.push({ id: `e-${h.id}-${w.id}`, fromId: `hub::${h.id}`, toId: nid, runState: 'idle' });
    });
  });

  // Layers — left column
  const layerX = 30;
  topology.activeLayers.forEach((l, i) => {
    nodes.push({
      id: `layer::${l.id}`,
      kind: 'test-layer',
      label: l.name,
      sublabel: `order ${l.order}${l.enabled ? '' : ' · deaktiviert'}`,
      badge: l.enabled ? 'on' : 'off',
      x: layerX,
      y: layerY + i * (NODE_H_LAYER + 20),
      w: NODE_W_LAYER,
      h: NODE_H_LAYER,
      runState: 'idle',
      payload: l,
    });
    topology.hubs.forEach(h => {
      edges.push({ id: `e-layer-${h.id}-${l.id}`, fromId: `hub::${h.id}`, toId: `layer::${l.id}`, label: 'governs', runState: 'idle' });
    });
  });

  // Routing rules — right column
  const ruleX = canvasW - NODE_W_RULE - 20;
  topology.routingRules.slice(0, 6).forEach((r, i) => {
    nodes.push({
      id: `rule::${r.id}`,
      kind: 'routing-rule',
      label: r.description.length > 20 ? r.description.slice(0, 18) + '…' : r.description,
      sublabel: `→ ${r.selectedBackend}`,
      badge: `p${r.priority}`,
      x: ruleX,
      y: ruleY + i * (NODE_H_RULE + 16),
      w: NODE_W_RULE,
      h: NODE_H_RULE,
      runState: 'idle',
      payload: r,
    });
  });

  return { nodes, edges };
}

/** @deprecated Use buildTopologyGraph. */
export const autoLayout = buildTopologyGraph;
