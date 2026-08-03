export type OrganizationActivationRuntimeState = 'not_observed' | 'partial' | 'observed';
export type OrganizationActivationFactState = 'observed_true' | 'observed_false' | 'unknown';
export type OrganizationActivationTargetState = 'bound' | 'hub_selection_required' | 'unsatisfied';
export type OrganizationActivationRoleBindingState = 'bound' | 'candidate_only' | 'unavailable';
export type OrganizationActivationAssignmentCoverageState =
  | 'not_bound'
  | 'desired_covered'
  | 'minimum_covered'
  | 'unassigned'
  | 'understaffed';
export type OrganizationActivationTaskKind =
  | 'planning'
  | 'research'
  | 'prototype'
  | 'coding'
  | 'review'
  | 'testing'
  | 'documentation'
  | 'gate_review'
  | 'handoff'
  | 'release';

export interface OrganizationRoleActivationMap {
  readonly schema: 'organization_role_activation_map.v1';
  readonly organization_id: string;
  readonly definition_revision: string;
  readonly snapshot_hash: string | null;
  readonly snapshot_revision: number | null;
  readonly stale: boolean;
  readonly snapshot_reason_code: string;
  readonly router_owner: 'hub';
  readonly runtime_observation: {
    readonly state: OrganizationActivationRuntimeState;
    readonly reason_code: string;
    readonly task_state_included: boolean;
  };
  readonly summary: {
    readonly active_team_count: number;
    readonly workflow_step_count: number;
    readonly edge_count: number;
    readonly unbound_step_count: number;
    readonly runtime_bound_step_count: number;
    readonly task_ready_step_count: number;
    readonly hub_routed_step_count: number;
    readonly worker_executing_step_count: number;
  };
  readonly teams: readonly OrganizationRoleActivationTeam[];
  readonly edges: readonly OrganizationRoleActivationEdge[];
}

export interface OrganizationRoleActivationTeam {
  readonly team_unit_id: string;
  readonly team_unit_key: string;
  readonly team_name: string;
  readonly team_blueprint_ref: string;
  readonly lifecycle: 'active';
  readonly revision_binding: {
    readonly team_blueprint_content_hash: string;
    readonly workflow_content_hash: string;
  };
  readonly workflow: {
    readonly workflow_ref: string;
    readonly mode: 'gated' | 'strict_gated';
    readonly default_failure_policy: 'block' | 'manual';
    readonly steps: readonly OrganizationRoleActivationStep[];
  };
}

export interface OrganizationRoleActivationStep {
  readonly step_id: string;
  readonly step_ref: string;
  readonly title: string;
  readonly task_kind: OrganizationActivationTaskKind;
  readonly owner_role_ref: string;
  readonly target_team_selector: {
    readonly team_blueprint_ref: string;
    readonly cardinality: number;
    readonly routing: 'single' | 'parallel';
  };
  readonly depends_on: readonly string[];
  readonly inputs: readonly string[];
  readonly outputs: readonly string[];
  readonly gate: {
    readonly required: boolean;
    readonly acceptance_checks: readonly string[];
    readonly approval_role_ref: string | null;
    readonly independent_principal_required: boolean;
  };
  readonly failure_policy: 'block' | 'manual';
  readonly handoff_ref: string | null;
  readonly target_resolution: {
    readonly state: OrganizationActivationTargetState;
    readonly reason_code: string;
    readonly router_owner: 'hub';
    readonly candidate_team_unit_ids: readonly string[];
    readonly bound_team_unit_ids: readonly string[];
  };
  readonly role_binding: {
    readonly state: OrganizationActivationRoleBindingState;
    readonly reason_code: string;
    readonly owner_role_ref: string;
    readonly candidate_role_slot_ids: readonly string[];
    readonly bound_role_slot_ids: readonly string[];
    readonly assignment_coverage: {
      readonly state: OrganizationActivationAssignmentCoverageState;
      readonly reason_code: string;
      readonly required_count: number;
      readonly desired_count: number;
      readonly active_count: number;
    };
  };
  readonly activation: {
    readonly state: OrganizationActivationRuntimeState;
    readonly reason_code: string;
    readonly router_owner: 'hub';
    readonly rule: 'hub_route_on_workflow_start' | 'hub_route_after_dependencies';
    readonly reacts_to: readonly {
      readonly kind: 'hub_workflow_intake' | 'workflow_step_completion';
      readonly source_ref: string;
      readonly source_owner_role_ref: string | null;
    }[];
    readonly external_inputs: readonly string[];
    readonly declared_input_sources?: readonly {
      readonly artifacts: readonly string[];
      readonly source_step_ref: string;
      readonly source_owner_role_ref: string;
      readonly source_team_unit_id: string;
      readonly handoff_ref: string;
      readonly relation_key: string;
    }[];
    readonly runtime: {
      readonly binding: {
        readonly state: 'exact' | 'unknown';
        readonly reason_code: string;
        readonly task_ids: readonly string[];
      };
      readonly task_ready: OrganizationActivationRuntimeFact;
      readonly hub_routed: OrganizationActivationRuntimeFact;
      readonly worker_executing: OrganizationActivationRuntimeFact;
      readonly worker_job_count: number;
      readonly active_lease_count: number;
    };
  };
}

export interface OrganizationActivationRuntimeFact {
  readonly state: OrganizationActivationFactState;
  readonly reason_code: string;
  readonly observed_true_count: number;
  readonly observed_false_count: number;
  readonly unknown_count: number;
}

interface OrganizationRoleActivationEdgeBase {
  readonly edge_id: string;
  readonly source: {
    readonly kind: 'workflow_step' | 'organization_unit' | 'team_unit';
    readonly ref: string;
  };
  readonly reason_code: string;
}

export interface OrganizationRoleActivationUnblocksEdge extends OrganizationRoleActivationEdgeBase {
  readonly type: 'unblocks';
  readonly target: { readonly kind: 'workflow_step'; readonly ref: string };
  readonly metadata: Readonly<Record<string, never>>;
}

export interface OrganizationRoleActivationProducesInputEdge extends OrganizationRoleActivationEdgeBase {
  readonly type: 'produces_input';
  readonly target: { readonly kind: 'workflow_step'; readonly ref: string };
  readonly metadata: { readonly artifacts: readonly string[] };
}

export interface OrganizationRoleActivationRequiresGateEdge extends OrganizationRoleActivationEdgeBase {
  readonly type: 'requires_gate';
  readonly target: {
    readonly kind: 'role_template' | 'hub';
    readonly ref: string;
  };
  readonly metadata: {
    readonly acceptance_checks: readonly string[];
    readonly independent_principal_required: boolean;
  };
}

export interface OrganizationRoleActivationDeclaresHandoffEdge extends OrganizationRoleActivationEdgeBase {
  readonly type: 'declares_handoff';
  readonly source: {
    readonly kind: 'organization_unit' | 'team_unit';
    readonly ref: string;
  };
  readonly target: {
    readonly kind: 'organization_unit' | 'team_unit';
    readonly ref: string;
  };
  readonly metadata: {
    readonly relation_key: string;
    readonly handoff_ref: string;
    readonly dependency_policy: 'advisory' | 'declared' | 'gate';
    readonly required_artifact_kinds: readonly string[];
    readonly acceptance_gate_ref: string;
  };
}

export type OrganizationRoleActivationEdge =
  | OrganizationRoleActivationUnblocksEdge
  | OrganizationRoleActivationProducesInputEdge
  | OrganizationRoleActivationRequiresGateEdge
  | OrganizationRoleActivationDeclaresHandoffEdge;

export interface OrganizationRoleActivationStepView {
  readonly team: OrganizationRoleActivationTeam;
  readonly step: OrganizationRoleActivationStep;
}

export function roleActivationContractError(
  value: unknown,
  expectedOrganizationId: string,
): string | null {
  if (!isRecord(value)) return 'response_not_object';
  if (value['schema'] !== 'organization_role_activation_map.v1') return 'schema_invalid';
  if (value['organization_id'] !== expectedOrganizationId) return 'organization_scope_mismatch';
  if (!nonEmptyString(value['definition_revision'])) return 'definition_revision_missing';
  if (value['snapshot_hash'] !== null && !nonEmptyString(value['snapshot_hash'])) return 'snapshot_hash_invalid';
  if (value['snapshot_revision'] !== null && !nonNegativeInteger(value['snapshot_revision'], 1)) {
    return 'snapshot_revision_invalid';
  }
  if (typeof value['stale'] !== 'boolean' || !nonEmptyString(value['snapshot_reason_code'])) {
    return 'snapshot_state_invalid';
  }
  if (value['router_owner'] !== 'hub') return 'router_owner_invalid';
  if (!isRuntimeObservation(value['runtime_observation'])) return 'runtime_observation_invalid';
  if (!isSummary(value['summary'])) return 'summary_invalid';
  if (!Array.isArray(value['teams']) || !value['teams'].every(isActivationTeam)) return 'teams_invalid';
  if (!Array.isArray(value['edges']) || !value['edges'].every(isActivationEdge)) return 'edges_invalid';
  return null;
}

function isActivationTeam(value: unknown): boolean {
  if (!isRecord(value) || value['lifecycle'] !== 'active') return false;
  if (![value['team_unit_id'], value['team_unit_key'], value['team_name'], value['team_blueprint_ref']]
    .every(nonEmptyString)) return false;
  const binding = value['revision_binding'];
  const workflow = value['workflow'];
  return isRecord(binding)
    && nonEmptyString(binding['team_blueprint_content_hash'])
    && nonEmptyString(binding['workflow_content_hash'])
    && isRecord(workflow)
    && nonEmptyString(workflow['workflow_ref'])
    && includes(['gated', 'strict_gated'], workflow['mode'])
    && includes(['block', 'manual'], workflow['default_failure_policy'])
    && Array.isArray(workflow['steps'])
    && workflow['steps'].every(isActivationStep);
}

function isActivationStep(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const selector = value['target_team_selector'];
  const gate = value['gate'];
  const target = value['target_resolution'];
  const role = value['role_binding'];
  const activation = value['activation'];
  return [value['step_id'], value['step_ref'], value['title'], value['owner_role_ref']]
    .every(nonEmptyString)
    && includes(ACTIVATION_TASK_KINDS, value['task_kind'])
    && isRecord(selector)
    && nonEmptyString(selector['team_blueprint_ref'])
    && nonNegativeInteger(selector['cardinality'], 1)
    && includes(['single', 'parallel'], selector['routing'])
    && stringArray(value['depends_on'])
    && stringArray(value['inputs'])
    && stringArray(value['outputs'])
    && isRecord(gate)
    && typeof gate['required'] === 'boolean'
    && stringArray(gate['acceptance_checks'])
    && (gate['approval_role_ref'] === null || nonEmptyString(gate['approval_role_ref']))
    && typeof gate['independent_principal_required'] === 'boolean'
    && includes(['block', 'manual'], value['failure_policy'])
    && (value['handoff_ref'] === null || nonEmptyString(value['handoff_ref']))
    && isTargetResolution(target)
    && isRoleBinding(role)
    && isActivationMetadata(activation);
}

function isTargetResolution(value: unknown): boolean {
  return isRecord(value)
    && includes(['bound', 'hub_selection_required', 'unsatisfied'], value['state'])
    && nonEmptyString(value['reason_code'])
    && value['router_owner'] === 'hub'
    && stringArray(value['candidate_team_unit_ids'])
    && stringArray(value['bound_team_unit_ids']);
}

function isRoleBinding(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const coverage = value['assignment_coverage'];
  return includes(['bound', 'candidate_only', 'unavailable'], value['state'])
    && nonEmptyString(value['reason_code'])
    && nonEmptyString(value['owner_role_ref'])
    && stringArray(value['candidate_role_slot_ids'])
    && stringArray(value['bound_role_slot_ids'])
    && isRecord(coverage)
    && includes(
      ['not_bound', 'desired_covered', 'minimum_covered', 'unassigned', 'understaffed'],
      coverage['state'],
    )
    && nonEmptyString(coverage['reason_code'])
    && nonNegativeInteger(coverage['required_count'])
    && nonNegativeInteger(coverage['desired_count'])
    && nonNegativeInteger(coverage['active_count']);
}

function isActivationMetadata(value: unknown): boolean {
  return isRecord(value)
    && value['state'] === 'not_observed'
    && nonEmptyString(value['reason_code'])
    && value['router_owner'] === 'hub'
    && includes(['hub_route_on_workflow_start', 'hub_route_after_dependencies'], value['rule'])
    && Array.isArray(value['reacts_to'])
    && value['reacts_to'].every(source => (
      isRecord(source)
      && includes(['hub_workflow_intake', 'workflow_step_completion'], source['kind'])
      && nonEmptyString(source['source_ref'])
      && (source['source_owner_role_ref'] === null || nonEmptyString(source['source_owner_role_ref']))
    ))
    && stringArray(value['external_inputs'])
    && (
      value['declared_input_sources'] === undefined
      || (
        Array.isArray(value['declared_input_sources'])
        && value['declared_input_sources'].every(source => (
          isRecord(source)
          && stringArray(source['artifacts'])
          && nonEmptyString(source['source_step_ref'])
          && nonEmptyString(source['source_owner_role_ref'])
          && nonEmptyString(source['source_team_unit_id'])
          && nonEmptyString(source['handoff_ref'])
          && nonEmptyString(source['relation_key'])
        ))
      )
    )
    && isStepRuntime(value['runtime']);
}

function isStepRuntime(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const binding = value['binding'];
  return isRecord(binding)
    && includes(['exact', 'unknown'], binding['state'])
    && nonEmptyString(binding['reason_code'])
    && stringArray(binding['task_ids'])
    && isRuntimeFact(value['task_ready'])
    && isRuntimeFact(value['hub_routed'])
    && isRuntimeFact(value['worker_executing'])
    && nonNegativeInteger(value['worker_job_count'])
    && nonNegativeInteger(value['active_lease_count']);
}

function isRuntimeFact(value: unknown): boolean {
  return isRecord(value)
    && includes(['observed_true', 'observed_false', 'unknown'], value['state'])
    && nonEmptyString(value['reason_code'])
    && nonNegativeInteger(value['observed_true_count'])
    && nonNegativeInteger(value['observed_false_count'])
    && nonNegativeInteger(value['unknown_count']);
}

function isActivationEdge(value: unknown): boolean {
  if (!isRecord(value)
    || !nonEmptyString(value['edge_id'])
    || !nonEmptyString(value['reason_code'])
    || !isRecord(value['metadata'])) return false;
  if (value['type'] === 'unblocks') {
    return isEndpoint(value['source'], ['workflow_step'])
      && isEndpoint(value['target'], ['workflow_step']);
  }
  if (value['type'] === 'produces_input') {
    return isEndpoint(value['source'], ['workflow_step'])
      && isEndpoint(value['target'], ['workflow_step'])
      && stringArray(value['metadata']['artifacts']);
  }
  if (value['type'] === 'requires_gate') {
    return isEndpoint(value['source'], ['workflow_step'])
      && isEndpoint(value['target'], ['role_template', 'hub'])
      && stringArray(value['metadata']['acceptance_checks'])
      && typeof value['metadata']['independent_principal_required'] === 'boolean';
  }
  if (value['type'] === 'declares_handoff') {
    return isEndpoint(value['source'], ['organization_unit', 'team_unit'])
      && isEndpoint(value['target'], ['organization_unit', 'team_unit'])
      && nonEmptyString(value['metadata']['relation_key'])
      && nonEmptyString(value['metadata']['handoff_ref'])
      && includes(['advisory', 'declared', 'gate'], value['metadata']['dependency_policy'])
      && stringArray(value['metadata']['required_artifact_kinds'])
      && nonEmptyString(value['metadata']['acceptance_gate_ref']);
  }
  return false;
}

function isRuntimeObservation(value: unknown): boolean {
  return isRecord(value)
    && includes(['not_observed', 'partial', 'observed'], value['state'])
    && nonEmptyString(value['reason_code'])
    && typeof value['task_state_included'] === 'boolean'
    && (value['state'] === 'not_observed' ? value['task_state_included'] === false : value['task_state_included']);
}

function isSummary(value: unknown): boolean {
  return isRecord(value)
    && [
      'active_team_count',
      'workflow_step_count',
      'edge_count',
      'unbound_step_count',
      'runtime_bound_step_count',
      'task_ready_step_count',
      'hub_routed_step_count',
      'worker_executing_step_count',
    ]
      .every(key => nonNegativeInteger(value[key]));
}

function isEndpoint(value: unknown, kinds: readonly string[]): boolean {
  return isRecord(value) && includes(kinds, value['kind']) && nonEmptyString(value['ref']);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(nonEmptyString);
}

function nonNegativeInteger(value: unknown, minimum = 0): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= minimum;
}

function includes(values: readonly string[], value: unknown): value is string {
  return typeof value === 'string' && values.includes(value);
}

const ACTIVATION_TASK_KINDS: readonly OrganizationActivationTaskKind[] = [
  'planning',
  'research',
  'prototype',
  'coding',
  'review',
  'testing',
  'documentation',
  'gate_review',
  'handoff',
  'release',
];
