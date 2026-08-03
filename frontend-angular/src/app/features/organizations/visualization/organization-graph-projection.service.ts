import { Injectable } from '@angular/core';

import {
  RenderEdgeStyle,
  RenderGraph,
  RenderNodeStyle,
} from '../../graph-rendering/models/render-graph.models';
import {
  OrganizationEdgeKind,
  OrganizationNodeKind,
  OrganizationRuntimeOverlay,
  OrganizationTopologyEdge,
  OrganizationTopologyNode,
} from '../models/organization-topology.models';
import {
  DEFAULT_ORGANIZATION_EDGE_KIND_COLORS,
  ORGANIZATION_LEADERSHIP_LABELS,
  ORGANIZATION_NODE_KIND_LABELS,
  OrganizationLeadershipScope,
  OrganizationRoleVisualOverride,
  OrganizationVisualProfile,
} from './organization-visual-profile.models';

export interface OrganizationVisualLegendEntry {
  readonly key: string;
  readonly label: string;
  readonly color: string;
  readonly count: number;
}

export interface OrganizationEdgeLegendEntry extends OrganizationVisualLegendEntry {
  readonly minimumWidth: number;
  readonly medianWidth: number;
  readonly maximumWidth: number;
}

export interface OrganizationSizeLegend {
  readonly metric: string;
  readonly references: readonly { readonly label: string; readonly value: number }[];
}

export interface OrganizationRoleVisualTarget {
  readonly key: string;
  readonly label: string;
  readonly roleTemplateRef: string | null;
  readonly teamLabel: string | null;
}

export interface OrganizationGraphProjection {
  readonly graph: RenderGraph;
  readonly nodeStyles: Readonly<Record<string, Readonly<RenderNodeStyle>>>;
  readonly edgeStyles: Readonly<Record<string, Readonly<RenderEdgeStyle>>>;
  readonly nodeLegend: readonly OrganizationVisualLegendEntry[];
  readonly edgeLegend: readonly OrganizationEdgeLegendEntry[];
  readonly sizeLegend: OrganizationSizeLegend;
  readonly activeMetrics: {
    readonly nodeColor: string;
    readonly edgeColor: string;
    readonly edgeStrength: string;
  };
  readonly assignmentStatus: {
    readonly observedRoleCount: number;
    readonly underAssignedRoleCount: number;
    readonly unknownRoleCount: number;
  };
  readonly roleTargets: readonly OrganizationRoleVisualTarget[];
}

const KIND_SIZE_SCORE: Readonly<Record<OrganizationNodeKind, number>> = Object.freeze({
  organization: 1,
  coordination_unit: 0.82,
  value_stream: 0.7,
  team: 0.58,
  role_slot: 0.38,
  assignment: 0.18,
});

const LEADERSHIP_SCORE: Readonly<Record<OrganizationLeadershipScope, number>> = Object.freeze({
  none: 0.2,
  team: 0.5,
  multi_team: 0.75,
  organization: 1,
});

const LEADERSHIP_COLORS: Readonly<Record<OrganizationLeadershipScope, string>> = Object.freeze({
  none: '#94A3B8',
  team: '#22C55E',
  multi_team: '#F59E0B',
  organization: '#EF4444',
});

const RUNTIME_COLORS: Readonly<Record<string, string>> = Object.freeze({
  active: '#22C55E',
  ready: '#14B8A6',
  pending: '#F59E0B',
  blocked: '#EF4444',
  failed: '#DC2626',
  completed: '#60A5FA',
  cancelled: '#64748B',
  idle: '#94A3B8',
  unknown: '#A78BFA',
});

const IMPORTANCE_COLORS = ['#64748B', '#38BDF8', '#22C55E', '#F59E0B', '#EF4444'] as const;
const UNSAFE_OVERRIDE_KEYS = new Set(['__proto__', 'prototype', 'constructor']);

@Injectable()
export class OrganizationGraphProjectionService {
  project(
    nodes: readonly OrganizationTopologyNode[],
    edges: readonly OrganizationTopologyEdge[],
    runtime: OrganizationRuntimeOverlay | null,
    profile: Readonly<OrganizationVisualProfile>,
  ): OrganizationGraphProjection {
    const nodeById = new Map(nodes.map(node => [node.id, node]));
    const runtimeByNode = new Map((runtime?.nodes ?? []).map(item => [item.node_id, item.status]));
    const degree = degreeScores(nodes, edges);
    const maxRuntimeProvenance = Math.max(
      1,
      ...edges.map(edge => finiteMetadataNumber(edge.metadata, 'provenance_count', 1)),
    );
    const nodeStyles: Record<string, Readonly<RenderNodeStyle>> = Object.create(null);
    const edgeStyles: Record<string, Readonly<RenderEdgeStyle>> = Object.create(null);
    const nodeLegend = new Map<string, OrganizationVisualLegendEntry>();
    const edgeLegend = new Map<string, OrganizationEdgeLegendEntry>();
    const edgeWidthSamples = new Map<string, number[]>();

    const renderNodes = nodes.map(node => {
      const override = roleOverride(node, profile);
      const runtimeStatus = runtimeByNode.get(node.id);
      const score = nodeScore(node, override, runtimeStatus, degree.get(node.id) ?? 0, profile);
      const size = interpolate(profile.nodeSizeRange.min, profile.nodeSizeRange.max, score);
      const category = nodeColorCategory(node, override, runtimeStatus?.state, profile);
      const color = override?.color ?? category.color;
      nodeStyles[node.id] = {
        color,
        size,
        highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
      };
      incrementLegend(
        nodeLegend,
        override?.color ? `${category.key}:role-override:${color}` : category.key,
        override?.color ? `${category.label} · explizite Rollenfarbe` : category.label,
        color,
      );
      return {
        id: node.id,
        label: node.label,
        kind: node.kind,
        tooltip: nodeTooltip(node, override, runtimeStatus?.label, size),
      };
    });

    const renderEdges = edges.map(edge => {
      const visualKind = presentationEdgeKind(edge, nodeById);
      const color = profile.edgeColorMetric === 'kind'
        ? profile.edgeKindColors[visualKind] ?? DEFAULT_ORGANIZATION_EDGE_KIND_COLORS[visualKind]
        : profile.edgeNamespaceColors[edge.namespace];
      const score = edgeScore(edge, visualKind, maxRuntimeProvenance, profile);
      const width = interpolate(profile.edgeWidthRange.min, profile.edgeWidthRange.max, score);
      edgeStyles[edge.id] = {
        color,
        width,
        highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
      };
      const legendKey = `${edge.namespace}:${visualKind}`;
      const samples = [...(edgeWidthSamples.get(legendKey) ?? []), width].sort((left, right) => left - right);
      edgeWidthSamples.set(legendKey, samples);
      const current = edgeLegend.get(legendKey);
      edgeLegend.set(legendKey, {
        key: legendKey,
        label: edgeKindLabel(visualKind),
        color,
        count: (current?.count ?? 0) + 1,
        minimumWidth: Math.min(current?.minimumWidth ?? width, width),
        medianWidth: median(samples),
        maximumWidth: Math.max(current?.maximumWidth ?? width, width),
      });
      const source = nodeById.get(edge.source_id)?.label ?? edge.source_id;
      const target = nodeById.get(edge.target_id)?.label ?? edge.target_id;
      const label = edge.label || edgeKindLabel(visualKind);
      return {
        id: edge.id,
        sourceId: edge.source_id,
        targetId: edge.target_id,
        kind: visualKind,
        label,
        tooltip: `${label}: ${source} → ${target}. Visuelle Stärke ${width.toFixed(2)}.`,
      };
    });

    return {
      graph: { nodes: renderNodes, edges: renderEdges },
      nodeStyles: Object.freeze(nodeStyles),
      edgeStyles: Object.freeze(edgeStyles),
      nodeLegend: [...nodeLegend.values()].sort((left, right) => left.label.localeCompare(right.label)),
      edgeLegend: [...edgeLegend.values()].sort((left, right) => left.label.localeCompare(right.label)),
      sizeLegend: {
        metric: nodeSizeMetricLabel(profile.nodeSizeMetric),
        references: [
          { label: 'klein', value: profile.nodeSizeRange.min },
          { label: 'mittel', value: interpolate(profile.nodeSizeRange.min, profile.nodeSizeRange.max, 0.5) },
          { label: 'groß', value: profile.nodeSizeRange.max },
        ],
      },
      activeMetrics: {
        nodeColor: nodeColorMetricLabel(profile.nodeColorMetric),
        edgeColor: edgeColorMetricLabel(profile.edgeColorMetric),
        edgeStrength: edgeStrengthMetricLabel(profile.edgeStrengthMetric),
      },
      assignmentStatus: assignmentStatus(nodes, runtimeByNode),
      roleTargets: roleTargets(nodes, nodeById),
    };
  }
}

function assignmentStatus(
  nodes: readonly OrganizationTopologyNode[],
  runtimeByNode: ReadonlyMap<string, { capacity_used?: number; capacity_limit?: number }>,
): OrganizationGraphProjection['assignmentStatus'] {
  let observedRoleCount = 0;
  let underAssignedRoleCount = 0;
  let unknownRoleCount = 0;
  for (const node of nodes) {
    if (node.kind !== 'role_slot') continue;
    const status = runtimeByNode.get(node.id);
    const used = Number(status?.capacity_used);
    const desired = nonNegativeMetadataInteger(node.metadata, 'default_count');
    if (!Number.isFinite(used) || used < 0 || desired === null) {
      unknownRoleCount += 1;
      continue;
    }
    observedRoleCount += 1;
    if (used < desired) underAssignedRoleCount += 1;
  }
  return { observedRoleCount, underAssignedRoleCount, unknownRoleCount };
}

function degreeScores(
  nodes: readonly OrganizationTopologyNode[],
  edges: readonly OrganizationTopologyEdge[],
): ReadonlyMap<string, number> {
  const degree = new Map(nodes.map(node => [node.id, 0]));
  edges.forEach(edge => {
    degree.set(edge.source_id, (degree.get(edge.source_id) ?? 0) + 1);
    degree.set(edge.target_id, (degree.get(edge.target_id) ?? 0) + 1);
  });
  const maximum = Math.max(1, ...degree.values());
  return new Map([...degree].map(([id, value]) => [id, value / maximum]));
}

function roleOverride(
  node: OrganizationTopologyNode,
  profile: Readonly<OrganizationVisualProfile>,
): Readonly<OrganizationRoleVisualOverride> | null {
  if (node.kind !== 'role_slot') return null;
  const templateRef = metadataString(node.metadata, 'role_template_ref');
  return explicitRoleOverride(profile.roleOverrides, node.stable_key)
    ?? explicitRoleOverride(profile.roleOverrides, templateRef)
    ?? null;
}

function explicitRoleOverride(
  overrides: Readonly<Record<string, Readonly<OrganizationRoleVisualOverride>>>,
  key: string,
): Readonly<OrganizationRoleVisualOverride> | undefined {
  if (!key || UNSAFE_OVERRIDE_KEYS.has(key.toLocaleLowerCase())) return undefined;
  return Object.prototype.hasOwnProperty.call(overrides, key) ? overrides[key] : undefined;
}

function nodeScore(
  node: OrganizationTopologyNode,
  override: Readonly<OrganizationRoleVisualOverride> | null,
  runtime: { capacity_used?: number; capacity_limit?: number; blocker_count?: number; gate_count?: number; handoff_count?: number } | undefined,
  degree: number,
  profile: Readonly<OrganizationVisualProfile>,
): number {
  switch (profile.nodeSizeMetric) {
    case 'importance':
      return node.kind === 'role_slot' ? (override?.importance ?? 50) / 100 : KIND_SIZE_SCORE[node.kind];
    case 'leadership_scope':
      return node.kind === 'role_slot'
        ? LEADERSHIP_SCORE[override?.leadershipScope ?? 'none']
        : KIND_SIZE_SCORE[node.kind];
    case 'runtime_load': {
      if (runtime?.capacity_limit && runtime.capacity_limit > 0) {
        return clamp01((runtime.capacity_used ?? 0) / runtime.capacity_limit);
      }
      const events = (runtime?.blocker_count ?? 0) + (runtime?.gate_count ?? 0) + (runtime?.handoff_count ?? 0);
      return clamp01(events / 10);
    }
    case 'degree':
      return clamp01(degree);
    case 'kind':
    default:
      return KIND_SIZE_SCORE[node.kind];
  }
}

function nodeColorCategory(
  node: OrganizationTopologyNode,
  override: Readonly<OrganizationRoleVisualOverride> | null,
  runtimeState: string | undefined,
  profile: Readonly<OrganizationVisualProfile>,
): { key: string; label: string; color: string } {
  switch (profile.nodeColorMetric) {
    case 'importance': {
      const importance = node.kind === 'role_slot' ? override?.importance ?? 50 : KIND_SIZE_SCORE[node.kind] * 100;
      const band = Math.min(4, Math.floor(clamp01(importance / 100) * 5));
      return { key: `importance:${band}`, label: `Wichtigkeit Stufe ${band + 1}`, color: IMPORTANCE_COLORS[band] };
    }
    case 'leadership_scope': {
      const scope = node.kind === 'role_slot' ? override?.leadershipScope ?? 'none' : 'none';
      return { key: `leadership:${scope}`, label: ORGANIZATION_LEADERSHIP_LABELS[scope], color: LEADERSHIP_COLORS[scope] };
    }
    case 'runtime_state': {
      const state = runtimeState || 'idle';
      return { key: `runtime:${state}`, label: `Runtime: ${state}`, color: RUNTIME_COLORS[state] ?? RUNTIME_COLORS['unknown'] };
    }
    case 'kind':
    default:
      return { key: `kind:${node.kind}`, label: ORGANIZATION_NODE_KIND_LABELS[node.kind], color: profile.nodeKindColors[node.kind] };
  }
}

function presentationEdgeKind(
  edge: OrganizationTopologyEdge,
  nodeById: ReadonlyMap<string, OrganizationTopologyNode>,
): OrganizationEdgeKind {
  return edge.kind === 'contains'
    && nodeById.get(edge.source_id)?.kind === 'role_slot'
    && nodeById.get(edge.target_id)?.kind === 'assignment'
    ? 'assignment'
    : edge.kind;
}

function edgeScore(
  edge: OrganizationTopologyEdge,
  kind: OrganizationEdgeKind,
  maxRuntimeProvenance: number,
  profile: Readonly<OrganizationVisualProfile>,
): number {
  if (profile.edgeStrengthMetric === 'fixed') return 0.35;
  if (profile.edgeStrengthMetric === 'runtime_provenance' && edge.namespace === 'runtime') {
    const provenance = finiteMetadataNumber(edge.metadata, 'provenance_count', 1);
    return clamp01(Math.log1p(provenance) / Math.log1p(maxRuntimeProvenance));
  }
  return clamp01(profile.edgeKindWeights[kind] ?? 0.4);
}

function incrementLegend(
  entries: Map<string, OrganizationVisualLegendEntry>,
  key: string,
  label: string,
  color: string,
): void {
  const current = entries.get(key);
  entries.set(key, { key, label, color, count: (current?.count ?? 0) + 1 });
}

function nodeTooltip(
  node: OrganizationTopologyNode,
  override: Readonly<OrganizationRoleVisualOverride> | null,
  runtimeLabel: string | undefined,
  size: number,
): string {
  const parts = [ORGANIZATION_NODE_KIND_LABELS[node.kind], node.label];
  if (override) {
    parts.push(`Wichtigkeit ${override.importance}/100`);
    parts.push(`Führungsebene ${ORGANIZATION_LEADERSHIP_LABELS[override.leadershipScope]}`);
  }
  if (runtimeLabel) parts.push(`Runtime ${runtimeLabel}`);
  parts.push(`Visuelle Größe ${size.toFixed(1)}`);
  return parts.join(' · ');
}

function roleTargets(
  nodes: readonly OrganizationTopologyNode[],
  nodeById: ReadonlyMap<string, OrganizationTopologyNode>,
): readonly OrganizationRoleVisualTarget[] {
  return nodes
    .filter(node => node.kind === 'role_slot')
    .map(node => ({
      key: node.stable_key,
      label: node.label,
      roleTemplateRef: metadataString(node.metadata, 'role_template_ref') || null,
      teamLabel: nearestTeamLabel(node, nodeById),
    }))
    .sort((left, right) => (
      (left.teamLabel ?? '').localeCompare(right.teamLabel ?? '')
      || left.label.localeCompare(right.label)
      || left.key.localeCompare(right.key)
    ));
}

function nearestTeamLabel(
  node: OrganizationTopologyNode,
  nodeById: ReadonlyMap<string, OrganizationTopologyNode>,
): string | null {
  let parentId = node.parent_id ?? null;
  const visited = new Set<string>();
  while (parentId && !visited.has(parentId)) {
    visited.add(parentId);
    const parent = nodeById.get(parentId);
    if (!parent) return null;
    if (parent.kind === 'team') return parent.label;
    parentId = parent.parent_id ?? null;
  }
  return null;
}

function nodeSizeMetricLabel(metric: OrganizationVisualProfile['nodeSizeMetric']): string {
  return ({
    kind: 'Knotentyp',
    importance: 'Explizite Wichtigkeit',
    leadership_scope: 'Explizite Führungsebene',
    runtime_load: 'Runtime-Auslastung',
    degree: 'Verbindungsgrad',
  } as const)[metric];
}

function nodeColorMetricLabel(metric: OrganizationVisualProfile['nodeColorMetric']): string {
  return ({
    kind: 'Knotentyp',
    importance: 'Explizite Wichtigkeit',
    leadership_scope: 'Explizite Führungsebene',
    runtime_state: 'Runtime-Status',
  } as const)[metric];
}

function edgeColorMetricLabel(metric: OrganizationVisualProfile['edgeColorMetric']): string {
  return metric === 'kind' ? 'Kantenart' : 'Namespace';
}

function edgeStrengthMetricLabel(metric: OrganizationVisualProfile['edgeStrengthMetric']): string {
  return ({
    fixed: 'Einheitlich',
    kind_weight: 'Explizites Gewicht je Kantenart',
    runtime_provenance: 'Runtime-Provenienz',
  } as const)[metric];
}

function edgeKindLabel(kind: OrganizationEdgeKind): string {
  return ({
    contains: 'Enthält', assignment: 'Besetzung', governs: 'Steuert', enables: 'Ermöglicht',
    supplies_research_to: 'Liefert Forschung', prototypes_for: 'Prototypisiert', reviews: 'Prüft',
    releases_for: 'Release für', declared_dependency: 'Deklarierte Abhängigkeit',
    runtime_task_dependency: 'Runtime-Task-Abhängigkeit', handoff: 'Übergabe',
    handoff_instance: 'Runtime-Übergabe', gate_state: 'Gate-Status', escalates_to: 'Eskaliert an',
    escalation_event: 'Runtime-Eskalation',
  } as const)[kind];
}

function metadataString(metadata: Readonly<Record<string, unknown>> | undefined, key: string): string {
  const value = metadata?.[key];
  return typeof value === 'string' ? value.trim() : '';
}

function finiteMetadataNumber(
  metadata: Readonly<Record<string, unknown>> | undefined,
  key: string,
  fallback: number,
): number {
  const value = Number(metadata?.[key]);
  return Number.isFinite(value) && value >= 0 ? value : fallback;
}

function nonNegativeMetadataInteger(
  metadata: Readonly<Record<string, unknown>> | undefined,
  key: string,
): number | null {
  const value = metadata?.[key];
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;
}

function interpolate(minimum: number, maximum: number, score: number): number {
  return minimum + (maximum - minimum) * clamp01(score);
}

function median(sortedValues: readonly number[]): number {
  if (!sortedValues.length) return 0;
  const middle = Math.floor(sortedValues.length / 2);
  return sortedValues.length % 2
    ? sortedValues[middle]
    : (sortedValues[middle - 1] + sortedValues[middle]) / 2;
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
}
