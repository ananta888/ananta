import { OrganizationEdgeKind, OrganizationEdgeNamespace, OrganizationNodeKind } from '../models/organization-topology.models';

export const ORGANIZATION_VISUAL_PROFILE_SCHEMA_VERSION = 1 as const;

export type OrganizationLeadershipScope = 'none' | 'team' | 'multi_team' | 'organization';
export type OrganizationNodeSizeMetric = 'kind' | 'importance' | 'leadership_scope' | 'runtime_load' | 'degree';
export type OrganizationNodeColorMetric = 'kind' | 'importance' | 'leadership_scope' | 'runtime_state';
export type OrganizationEdgeColorMetric = 'namespace' | 'kind';
export type OrganizationEdgeStrengthMetric = 'fixed' | 'kind_weight' | 'runtime_provenance';

export interface OrganizationVisualRange {
  readonly min: number;
  readonly max: number;
}

export interface OrganizationRoleVisualOverride {
  readonly importance: number;
  readonly leadershipScope: OrganizationLeadershipScope;
  readonly color?: string;
}

export interface OrganizationVisualProfile {
  readonly schemaVersion: typeof ORGANIZATION_VISUAL_PROFILE_SCHEMA_VERSION;
  readonly nodeSizeMetric: OrganizationNodeSizeMetric;
  readonly nodeColorMetric: OrganizationNodeColorMetric;
  readonly edgeColorMetric: OrganizationEdgeColorMetric;
  readonly edgeStrengthMetric: OrganizationEdgeStrengthMetric;
  readonly nodeSizeRange: Readonly<OrganizationVisualRange>;
  readonly edgeWidthRange: Readonly<OrganizationVisualRange>;
  readonly nodeKindColors: Readonly<Record<OrganizationNodeKind, string>>;
  readonly edgeNamespaceColors: Readonly<Record<OrganizationEdgeNamespace, string>>;
  readonly edgeKindColors: Readonly<Partial<Record<OrganizationEdgeKind, string>>>;
  readonly edgeKindWeights: Readonly<Partial<Record<OrganizationEdgeKind, number>>>;
  readonly roleOverrides: Readonly<Record<string, Readonly<OrganizationRoleVisualOverride>>>;
}

export const ORGANIZATION_NODE_KIND_LABELS: Readonly<Record<OrganizationNodeKind, string>> = Object.freeze({
  organization: 'Organisation',
  coordination_unit: 'Koordination',
  value_stream: 'Value Stream',
  team: 'Team',
  role_slot: 'Rolle',
  assignment: 'Zuweisung',
});

export const ORGANIZATION_LEADERSHIP_LABELS: Readonly<Record<OrganizationLeadershipScope, string>> = Object.freeze({
  none: 'Keine explizite Führungsebene',
  team: 'Team',
  multi_team: 'Mehrere Teams',
  organization: 'Organisation',
});

export const DEFAULT_ORGANIZATION_EDGE_KIND_COLORS: Readonly<Record<OrganizationEdgeKind, string>> = Object.freeze({
  contains: '#94A3B8',
  governs: '#818CF8',
  enables: '#14B8A6',
  supplies_research_to: '#38BDF8',
  prototypes_for: '#A78BFA',
  reviews: '#F472B6',
  releases_for: '#22C55E',
  declared_dependency: '#60A5FA',
  runtime_task_dependency: '#F97316',
  handoff: '#2DD4BF',
  handoff_instance: '#0EA5E9',
  gate_state: '#EAB308',
  escalates_to: '#FB7185',
  escalation_event: '#EF4444',
  assignment: '#FBBF24',
});

export const DEFAULT_ORGANIZATION_VISUAL_PROFILE: Readonly<OrganizationVisualProfile> = deepFreeze({
  schemaVersion: ORGANIZATION_VISUAL_PROFILE_SCHEMA_VERSION,
  nodeSizeMetric: 'kind',
  nodeColorMetric: 'kind',
  edgeColorMetric: 'namespace',
  edgeStrengthMetric: 'kind_weight',
  nodeSizeRange: { min: 5, max: 24 },
  edgeWidthRange: { min: 0.75, max: 6 },
  nodeKindColors: {
    organization: '#60A5FA',
    coordination_unit: '#818CF8',
    value_stream: '#22D3EE',
    team: '#34D399',
    role_slot: '#C084FC',
    assignment: '#FBBF24',
  },
  edgeNamespaceColors: {
    hierarchy: '#94A3B8',
    organization: '#38BDF8',
    runtime: '#FB7185',
  },
  edgeKindColors: DEFAULT_ORGANIZATION_EDGE_KIND_COLORS,
  edgeKindWeights: {
    contains: 0.15,
    assignment: 0.35,
    governs: 0.7,
    reviews: 0.65,
    declared_dependency: 0.55,
    handoff: 0.6,
    escalates_to: 0.8,
    runtime_task_dependency: 0.75,
  },
  roleOverrides: {},
});

function deepFreeze<T>(value: T): Readonly<T> {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value as Record<string, unknown>).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}
