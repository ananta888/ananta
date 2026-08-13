import type {
  ModelRoutingConfig,
  VpGraph,
  VpStep,
} from '../../visual-process/visual-process-api.service';
import type {
  CaseFlowAgentRoleId,
} from './caseflow-role-catalog';

/** Icons are part of the persisted v1 presentation contract. */
export const CASEFLOW_AGENT_ICON_ALLOWLIST = [
  'account_tree',
  'architecture',
  'code',
  'web',
  'dns',
  'cloud',
  'bug_report',
  'security',
  'rate_review',
  'assignment_turned_in',
  'groups',
  'palette',
  'design_services',
  'accessibility_new',
  'edit_note',
  'videocam',
  'graphic_eq',
  'campaign',
  'point_of_sale',
  'account_balance',
  'gavel',
  'badge',
  'analytics',
  'science',
  'event_note',
  'star',
  'engineering',
  'rule',
  'approval',
  'visibility',
  'person',
] as const;

export type CaseFlowAgentIcon = typeof CASEFLOW_AGENT_ICON_ALLOWLIST[number];

const CASEFLOW_AGENT_ICONS = new Set<string>(CASEFLOW_AGENT_ICON_ALLOWLIST);

export function isAllowedCaseFlowAgentIcon(value: unknown): value is CaseFlowAgentIcon {
  return typeof value === 'string' && CASEFLOW_AGENT_ICONS.has(value);
}

/**
 * The persisted graph remains the only source of truth. This extension stores
 * presentation hints only; runtime state deliberately has no field here.
 */
export const CASEFLOW_AGENT_CANVAS_EXTENSION = 'ananta.caseflow.agent-canvas';
export const CASEFLOW_AGENT_CANVAS_SCHEMA_V1 = 'ananta.caseflow.agent-canvas/v1';

/** Declarative step bindings are references, never copied profile contents. */
export const CASEFLOW_AGENT_BINDINGS_METADATA = 'ananta.caseflow.agent-bindings';
export const CASEFLOW_AGENT_BINDINGS_SCHEMA_V1 = 'ananta.caseflow.agent-bindings/v1';

export type CaseFlowAgentInspectorTab =
  | 'configuration'
  | 'runtime'
  | 'communication'
  | 'trace';

export interface CaseFlowAgentInspectorHintsV1 {
  default_tab?: CaseFlowAgentInspectorTab;
  advanced_collapsed?: boolean;
  [futureField: string]: unknown;
}

export interface CaseFlowAgentNodePresentationV1 {
  icon?: CaseFlowAgentIcon;
  inspector_hints?: CaseFlowAgentInspectorHintsV1;
  [futureField: string]: unknown;
}

export interface CaseFlowAgentCanvasExtensionV1 {
  schema: typeof CASEFLOW_AGENT_CANVAS_SCHEMA_V1;
  nodes?: Record<string, CaseFlowAgentNodePresentationV1>;
  [futureField: string]: unknown;
}

export type CaseFlowPersonalityResourceType =
  | 'agent_profile'
  | 'instruction_layer';

export type CaseFlowContextResourceType =
  | 'context_profile'
  | 'context_source';

export interface CaseFlowPersonalityBindingRef {
  resource_type: CaseFlowPersonalityResourceType;
  resource_id: string;
  [futureField: string]: unknown;
}

export interface CaseFlowContextBindingRef {
  resource_type: CaseFlowContextResourceType;
  resource_id: string;
  [futureField: string]: unknown;
}

export interface CaseFlowAgentBindingsV1 {
  schema: typeof CASEFLOW_AGENT_BINDINGS_SCHEMA_V1;
  personality_binding?: CaseFlowPersonalityBindingRef;
  context_bindings?: CaseFlowContextBindingRef[];
  [futureField: string]: unknown;
}

/** Only reference-bearing routing fields managed by the compact inspector. */
export type CaseFlowModelRoutingBindings = Pick<
  ModelRoutingConfig,
  'model_role' | 'preferred_profile_id' | 'fallback_group_id'
>;

export interface CaseFlowAgentConfigurationProjection {
  skill_profile_id?: string;
  personality_binding?: CaseFlowPersonalityBindingRef;
  context_bindings: readonly CaseFlowContextBindingRef[];
  model_routing: CaseFlowModelRoutingBindings;
  policy_hints: readonly string[];
  human_gate: boolean;
}

export interface CaseFlowAgentCanvasNodeProjection {
  step_id: string;
  label: string;
  role: string;
  role_preset: CaseFlowAgentRoleId;
  icon: CaseFlowAgentIcon;
  position: Readonly<{ x: number; y: number }>;
  configuration: CaseFlowAgentConfigurationProjection;
  incoming_edge_ids: readonly string[];
  outgoing_edge_ids: readonly string[];
}

export interface CaseFlowAgentCanvasEdgeProjection {
  edge_id: string;
  source_step_id: string;
  target_step_id: string;
  label?: string;
  loop: boolean;
  feedback: boolean;
  reverse_edge_ids: readonly string[];
}

export interface CaseFlowAgentCanvasProjection {
  /**
   * Canonical input reference used for a lossless projection roundtrip. Canvas
   * commands replace this graph immutably instead of persisting projected DTOs.
   */
  canonical_graph: VpGraph;
  nodes: readonly CaseFlowAgentCanvasNodeProjection[];
  edges: readonly CaseFlowAgentCanvasEdgeProjection[];
}

export type CaseFlowAgentCanvasIssueCode =
  | 'agent_canvas_extension_invalid'
  | 'agent_canvas_schema_unsupported'
  | 'agent_canvas_node_invalid'
  | 'agent_binding_contract_invalid'
  | 'agent_binding_reference_invalid'
  | 'agent_binding_reference_not_allowed'
  | 'agent_model_routing_invalid'
  | 'agent_role_invalid'
  | 'agent_icon_not_allowed'
  | 'agent_step_not_found';

export interface CaseFlowAgentCanvasIssue {
  code: CaseFlowAgentCanvasIssueCode;
  path: string;
  message: string;
}

export type CaseFlowAgentCanvasResult<T> =
  | { ok: true; value: T; issues: readonly [] }
  | { ok: false; issues: readonly CaseFlowAgentCanvasIssue[] };

export function agentCanvasSuccess<T>(value: T): CaseFlowAgentCanvasResult<T> {
  return { ok: true, value, issues: [] };
}

export function agentCanvasFailure<T>(
  ...issues: CaseFlowAgentCanvasIssue[]
): CaseFlowAgentCanvasResult<T> {
  return { ok: false, issues };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function parseAgentCanvasExtension(
  graph: VpGraph,
): CaseFlowAgentCanvasResult<CaseFlowAgentCanvasExtensionV1> {
  const raw = graph.extensions?.[CASEFLOW_AGENT_CANVAS_EXTENSION];
  if (raw === undefined) {
    return agentCanvasSuccess({ schema: CASEFLOW_AGENT_CANVAS_SCHEMA_V1 });
  }
  return validateAgentCanvasExtension(raw);
}

export function validateAgentCanvasExtension(
  raw: unknown,
): CaseFlowAgentCanvasResult<CaseFlowAgentCanvasExtensionV1> {
  if (!isRecord(raw)) {
    return agentCanvasFailure({
      code: 'agent_canvas_extension_invalid',
      path: `/extensions/${CASEFLOW_AGENT_CANVAS_EXTENSION}`,
      message: 'The CaseFlow agent-canvas extension must be an object.',
    });
  }
  if (raw['schema'] !== CASEFLOW_AGENT_CANVAS_SCHEMA_V1) {
    return agentCanvasFailure({
      code: 'agent_canvas_schema_unsupported',
      path: `/extensions/${CASEFLOW_AGENT_CANVAS_EXTENSION}/schema`,
      message: 'The CaseFlow agent-canvas schema is missing or unsupported.',
    });
  }
  if (raw['nodes'] !== undefined && !isRecord(raw['nodes'])) {
    return agentCanvasFailure({
      code: 'agent_canvas_extension_invalid',
      path: `/extensions/${CASEFLOW_AGENT_CANVAS_EXTENSION}/nodes`,
      message: 'Agent presentation entries must be an object keyed by step id.',
    });
  }
  for (const [stepId, value] of Object.entries(raw['nodes'] ?? {})) {
    if (!isRecord(value)
      || (value['icon'] !== undefined && !isAllowedCaseFlowAgentIcon(value['icon']))
      || !isInspectorHints(value['inspector_hints'])) {
      return agentCanvasFailure({
        code: 'agent_canvas_node_invalid',
        path: `/extensions/${CASEFLOW_AGENT_CANVAS_EXTENSION}/nodes/${stepId}`,
        message: `Presentation data for step "${stepId}" is invalid.`,
      });
    }
  }
  return agentCanvasSuccess(raw as unknown as CaseFlowAgentCanvasExtensionV1);
}

export function serializeAgentCanvasExtension(
  graph: VpGraph,
  extension: CaseFlowAgentCanvasExtensionV1,
): CaseFlowAgentCanvasResult<VpGraph> {
  const validation = validateAgentCanvasExtension(extension);
  if (!validation.ok) return validation;
  return agentCanvasSuccess({
    ...graph,
    extensions: {
      ...(graph.extensions ?? {}),
      [CASEFLOW_AGENT_CANVAS_EXTENSION]: extension,
    },
  });
}

export function parseAgentBindings(
  step: VpStep,
): CaseFlowAgentCanvasResult<CaseFlowAgentBindingsV1> {
  const raw = step.metadata?.[CASEFLOW_AGENT_BINDINGS_METADATA];
  if (raw === undefined) {
    return agentCanvasSuccess({ schema: CASEFLOW_AGENT_BINDINGS_SCHEMA_V1 });
  }
  if (!isRecord(raw) || raw['schema'] !== CASEFLOW_AGENT_BINDINGS_SCHEMA_V1) {
    return agentCanvasFailure({
      code: 'agent_binding_contract_invalid',
      path: `/steps/${step.id}/metadata/${CASEFLOW_AGENT_BINDINGS_METADATA}`,
      message: 'The CaseFlow agent-binding contract is missing, malformed, or unsupported.',
    });
  }

  const personality = raw['personality_binding'];
  if (personality !== undefined && !isPersonalityBinding(personality)) {
    return agentCanvasFailure({
      code: 'agent_binding_contract_invalid',
      path: `/steps/${step.id}/metadata/${CASEFLOW_AGENT_BINDINGS_METADATA}/personality_binding`,
      message: 'The personality binding must reference an agent profile or instruction layer.',
    });
  }

  const contexts = raw['context_bindings'];
  if (contexts !== undefined
    && (!Array.isArray(contexts) || !contexts.every(isContextBinding))) {
    return agentCanvasFailure({
      code: 'agent_binding_contract_invalid',
      path: `/steps/${step.id}/metadata/${CASEFLOW_AGENT_BINDINGS_METADATA}/context_bindings`,
      message: 'Every context binding must reference a context profile or context source.',
    });
  }
  return agentCanvasSuccess(raw as unknown as CaseFlowAgentBindingsV1);
}

export function serializeAgentBindings(
  step: VpStep,
  bindings: CaseFlowAgentBindingsV1,
): CaseFlowAgentCanvasResult<VpStep> {
  const validation = validateAgentBindingsValue(step.id, bindings);
  if (!validation.ok) return validation;
  return agentCanvasSuccess({
    ...step,
    metadata: {
      ...(step.metadata ?? {}),
      [CASEFLOW_AGENT_BINDINGS_METADATA]: bindings,
    },
  });
}

export function readModelRoutingBindings(step: VpStep): CaseFlowAgentCanvasResult<CaseFlowModelRoutingBindings> {
  const raw = step.metadata?.['model_routing'];
  if (raw === undefined) return agentCanvasSuccess({});
  if (!isRecord(raw)) {
    return agentCanvasFailure({
      code: 'agent_model_routing_invalid',
      path: `/steps/${step.id}/metadata/model_routing`,
      message: 'Model routing must be an object.',
    });
  }
  const fields: Array<keyof CaseFlowModelRoutingBindings> = [
    'model_role',
    'preferred_profile_id',
    'fallback_group_id',
  ];
  const result: CaseFlowModelRoutingBindings = {};
  for (const field of fields) {
    const value = raw[field];
    if (value === undefined) continue;
    if (typeof value !== 'string' || !value.trim()) {
      return agentCanvasFailure({
        code: 'agent_model_routing_invalid',
        path: `/steps/${step.id}/metadata/model_routing/${field}`,
        message: `Model-routing reference "${field}" must be a non-empty string.`,
      });
    }
    result[field] = value;
  }
  return agentCanvasSuccess(result);
}

function validateAgentBindingsValue(
  stepId: string,
  bindings: CaseFlowAgentBindingsV1,
): CaseFlowAgentCanvasResult<CaseFlowAgentBindingsV1> {
  const fixture: VpStep = {
    id: stepId,
    label: '',
    kind: '',
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: [],
    gate: false,
    metadata: { [CASEFLOW_AGENT_BINDINGS_METADATA]: bindings },
  };
  const parsed = parseAgentBindings(fixture);
  return parsed.ok ? agentCanvasSuccess(bindings) : parsed;
}

function isPersonalityBinding(value: unknown): value is CaseFlowPersonalityBindingRef {
  return isRecord(value)
    && (value['resource_type'] === 'agent_profile' || value['resource_type'] === 'instruction_layer')
    && isNonEmptyString(value['resource_id']);
}

function isContextBinding(value: unknown): value is CaseFlowContextBindingRef {
  return isRecord(value)
    && (value['resource_type'] === 'context_profile' || value['resource_type'] === 'context_source')
    && isNonEmptyString(value['resource_id']);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim());
}

function isInspectorHints(value: unknown): boolean {
  if (value === undefined) return true;
  if (!isRecord(value)) return false;
  const tab = value['default_tab'];
  const advancedCollapsed = value['advanced_collapsed'];
  return (tab === undefined || (typeof tab === 'string' && [
    'configuration',
    'runtime',
    'communication',
    'trace',
  ].includes(tab)))
    && (advancedCollapsed === undefined || typeof advancedCollapsed === 'boolean');
}
