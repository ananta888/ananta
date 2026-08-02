export type OrganizationLifecycle =
  | 'draft'
  | 'validated'
  | 'active'
  | 'paused'
  | 'completed'
  | 'archived';

export type OrganizationViewMode = 'hierarchy' | 'graph';
export type OrganizationNodeKind =
  | 'organization'
  | 'coordination_unit'
  | 'value_stream'
  | 'team'
  | 'role_slot'
  | 'assignment';
export type OrganizationEdgeNamespace = 'hierarchy' | 'organization' | 'runtime';
export type OrganizationEdgeKind =
  | 'contains'
  | 'governs'
  | 'enables'
  | 'supplies_research_to'
  | 'prototypes_for'
  | 'reviews'
  | 'releases_for'
  | 'declared_dependency'
  | 'runtime_task_dependency'
  | 'handoff'
  | 'handoff_instance'
  | 'gate_state'
  | 'escalates_to'
  | 'escalation_event'
  | 'assignment';

export interface OrganizationLimitProfile {
  revision: string;
  policy_hash: string;
  max_teams: number;
  max_units: number;
  max_role_slots: number;
  max_assignments: number;
  max_relations: number;
  max_patch_operations: number;
  max_page_size: number;
  max_depth: number;
  max_render_nodes: number;
  max_render_edges: number;
}

export interface OrganizationBlueprintSummary {
  key: string;
  definition_key: string;
  version: string;
  title: string;
  description?: string;
  team_count: number;
  standard: boolean;
  recommended?: boolean;
  test_only?: boolean;
  activation_summary?: readonly string[];
  capabilities?: readonly string[];
  revision: string;
  supported_team_counts: readonly number[];
  custom_team_count_min: number;
  custom_team_count_max: number;
  custom_team_blueprints: readonly OrganizationCustomTeamBlueprintOption[];
}

export interface OrganizationCustomTeamBlueprintOption {
  key: string;
  version: string;
  title: string;
  repeatable: boolean;
  minimum_when_selected: number;
  maximum: number;
  standard_baseline: boolean;
}

export interface OrganizationSummary {
  id: string;
  key: string;
  title: string;
  lifecycle: OrganizationLifecycle;
  definition_revision: string;
  snapshot_hash: string;
  team_count: number;
  unit_count: number;
  project_id?: string;
  tenant_id?: string;
  lock_version: number;
  revision: string;
}

export interface OrganizationNodeStatus {
  state: string;
  label: string;
  reason_code?: string;
  capacity_used?: number;
  capacity_limit?: number;
  blocker_count?: number;
  gate_count?: number;
  handoff_count?: number;
  drift?: boolean;
}

export interface OrganizationTopologyNode {
  id: string;
  stable_key: string;
  kind: OrganizationNodeKind;
  label: string;
  parent_id?: string | null;
  depth: number;
  child_count: number;
  has_more_children?: boolean;
  team_id?: string;
  unit_id?: string;
  role_slot_id?: string;
  assignment_id?: string;
  capabilities?: readonly string[];
  metadata?: Readonly<Record<string, unknown>>;
}

export interface OrganizationTopologyEdge {
  id: string;
  namespace: OrganizationEdgeNamespace;
  kind: OrganizationEdgeKind;
  source_id: string;
  target_id: string;
  label?: string;
  read_only?: boolean;
  metadata?: Readonly<Record<string, unknown>>;
}

export interface OrganizationRuntimeNodeOverlay {
  node_id: string;
  status: OrganizationNodeStatus;
  latest_artifacts?: readonly {
    artifact_id: string;
    version: string;
    digest: string;
    label: string;
  }[];
}

export interface OrganizationRuntimeOverlay {
  definition_revision: string;
  snapshot_hash: string;
  generated_at: string;
  stale: boolean;
  nodes: readonly OrganizationRuntimeNodeOverlay[];
  edges: readonly OrganizationTopologyEdge[];
}

export interface OrganizationDiagnostic {
  severity: 'info' | 'warning' | 'blocker';
  reason_code: string;
  message: string;
  node_ids?: readonly string[];
  policy_id?: string;
}

export interface OrganizationTopologyPage {
  organization_id: string;
  definition_revision: string;
  snapshot_hash: string;
  nodes: readonly OrganizationTopologyNode[];
  edges: readonly OrganizationTopologyEdge[];
  runtime_overlay: OrganizationRuntimeOverlay | null;
  diagnostics: readonly OrganizationDiagnostic[];
  limits: OrganizationLimitProfile;
  next_cursor: string | null;
  truncated: boolean;
}

export interface OrganizationTopologyQuery {
  cursor?: string;
  page_size?: number;
  depth?: number;
  subgraph_root_id?: string;
  kinds?: readonly OrganizationNodeKind[];
  edge_namespaces?: readonly OrganizationEdgeNamespace[];
  search?: string;
  include_runtime?: boolean;
}

export interface OrganizationCompileRequest {
  blueprint_key: string;
  blueprint_version?: string;
  title: string;
  team_count?: number;
  custom_team_blueprint_keys?: readonly string[];
  admission_exception_ref?: string;
  parameters?: Readonly<Record<string, unknown>>;
}

export interface OrganizationAdmissionExceptionRequest {
  blueprint_version?: string;
  team_blueprint_counts: Readonly<Record<string, number>>;
  reason: string;
  ttl_seconds?: number;
}

export interface OrganizationAdmissionExceptionResult {
  admission_exception_ref: string;
  definition_ref: string;
  definition_revision: string;
  composition_digest: string;
  team_blueprint_counts: Readonly<Record<string, number>>;
  team_count: number;
  capability_gaps: readonly string[];
  policy_hash: string;
  status: 'issued' | 'consumed' | 'revoked';
  expires_at: number;
  replayed: boolean;
}

export interface OrganizationCompilePlan {
  blueprint_key: string;
  blueprint_version: string;
  title: string;
  organization_id: string;
  definition_ref: string;
  definition_revision: string;
  plan_digest: string;
  compile_token: string;
  expires_at: string;
  admin_policy_hash: string;
  composition_mode: 'standard' | 'custom';
  team_count: number;
  unit_count: number;
  hierarchy_edge_count: number;
  relation_edge_count: number;
  role_slot_count: number;
  planned_writes: readonly string[];
  capability_gaps: readonly string[];
  unfilled_required_slots: readonly string[];
  budget_assumptions: Readonly<Record<string, number>>;
  diagnostics: readonly OrganizationDiagnostic[];
  limits: OrganizationLimitProfile;
  admission_exception_ref?: string;
}

export interface OrganizationInstantiateRequest {
  compile_plan: OrganizationCompilePlan;
  title: string;
  admin_grant: string;
}

export interface OrganizationInstantiateResult {
  organization: OrganizationSummary;
  unit_ids: readonly string[];
  team_ids: readonly string[];
  organization_admin_grant_id: string;
  replayed: boolean;
}

export interface OrganizationRoleSlotPatchValue {
  stable_key: string;
  name: string;
  slot_key: string;
  role_template_ref: string;
  required: boolean;
  min_count: number;
  default_count: number;
  max_count: number | null;
  assignment_policy: {
    principal_kinds: readonly ('agent' | 'human')[];
    required_capabilities: readonly string[];
    forbidden_capabilities: readonly string[];
    write_access_required: boolean;
  };
  separation_of_duties: {
    enforcement: 'none' | 'warn' | 'strict';
    independent_from_slot_ids: readonly string[];
    independent_from_external_duties: readonly string[];
  };
  overlays: readonly string[];
}

export type OrganizationPatchOperation =
  | {
      op: 'add';
      node_kind: 'coordination_unit' | 'value_stream';
      parent_id: string;
      value: { stable_key: string; name: string };
    }
  | {
      op: 'add';
      node_kind: 'team';
      parent_id: string;
      value: { stable_key: string; name: string; team_blueprint_ref: string };
    }
  | {
      op: 'add';
      node_kind: 'role_slot';
      parent_id: string;
      value: OrganizationRoleSlotPatchValue;
    }
  | { op: 'remove'; node_id: string; lifecycle_strategy: 'drain' | 'archive' }
  | {
      op: 'remove';
      node_id: string;
      lifecycle_strategy: 'migrate';
      migration_target: {
        organization_id: string;
        unit_id: string;
        team_id: string;
        role_slot_id: string;
      };
    }
  | { op: 'reparent'; node_id: string; parent_id: string; lifecycle_strategy?: 'drain' | 'migrate' }
  | { op: 'connect'; namespace: 'organization'; edge_kind: 'declared_dependency' | 'handoff'; source_id: string; target_id: string }
  | { op: 'assign'; role_slot_id: string; agent_id: string };

export interface OrganizationPatchPreview {
  tenant_id: string;
  project_id: string;
  organization_id: string;
  principal_id: string;
  expected_revision: string;
  source_snapshot_hash: string;
  patch_digest: string;
  expires_at: string;
  expires_at_epoch: number;
  effective_limit_profile_ref: string;
  effective_limit_profile_revision: number;
  effective_limit_profile_hash: string;
  effective_policy_hash: string;
  budget_policy_hash: string;
  operations: readonly OrganizationPatchOperation[];
  planned_writes: readonly string[];
  diagnostics: readonly OrganizationDiagnostic[];
  limits: OrganizationLimitProfile;
  applicable: boolean;
}

export interface OrganizationTopologyPatchGrant {
  grant_id: string;
  grant_kind: 'topology_patch';
  tenant_id: string;
  project_id: string;
  organization_id: string;
  principal_id: string;
  patch_digest: string;
  policy_hash: string;
  limit_hash: string;
  expected_revision: string;
  expires_at: number;
  replayed: boolean;
}

export interface OrganizationRoleSlot {
  id: string;
  stable_key: string;
  role_template_key: string;
  role_template_version: string;
  label: string;
  scrum_accountability?: 'product_owner' | 'scrum_master' | 'developers';
  specialization?: string;
  min_count: number;
  default_count: number;
  max_count: number;
  required_capabilities: readonly string[];
  risk_level: string;
  independent_verification_required: boolean;
  assignments: readonly OrganizationAssignmentCandidate[];
}

export interface OrganizationAssignmentCandidate {
  agent_id: string;
  label: string;
  compatible: boolean;
  capacity_used: number;
  capacity_limit: number;
  affected_teams: readonly string[];
  reasons: readonly string[];
}

export interface OrganizationBundlePreview {
  plan_digest: string;
  expires_at: string;
  source_version: string;
  target_version: string;
  project_id: string;
  conflict_strategy: string;
  redacted_fields: readonly string[];
  omitted_fields: readonly string[];
  diagnostics: readonly OrganizationDiagnostic[];
  changes: Readonly<Record<string, readonly { key: string; action: string; detail?: string }[]>>;
  applicable: boolean;
  instance_import_mode: 'optional_target_recompile';
  target_rebind_contract: {
    available: boolean;
    scope_binding: 'authenticated_target_project';
    root_definition_ref: string | null;
    compile_endpoint_template: '/api/organization-blueprints/{blueprint_key}/compile';
    instantiate_endpoint: '/api/organizations';
    id_allocation: 'target_hub';
    assignment_binding: 'explicit_target_local_rebind';
  };
  migration_warnings: readonly string[];
  bundle: unknown;
  import_plan: OrganizationBundleImportPlanView;
}

export interface OrganizationBundleImportPlanView {
  expected_target_revision: string;
  effective_limit_profile_hash: string;
  assignment_rebindings: Readonly<Record<string, string>>;
  instance_admission_exception_refs: Readonly<Record<string, string>>;
  errors: readonly OrganizationDiagnostic[];
  [field: string]: unknown;
}

export interface OrganizationBundleGrant {
  grant_id: string;
  grant_kind: 'bundle_import';
  plan_digest: string;
  policy_hash: string;
  expires_at: number;
  replayed: boolean;
}

export interface OrganizationDefinitionGraphBundle {
  schema_version: '2.0';
  bundle_metadata: {
    export_kind: 'organization_definition_graph' | 'organization_recompile_bundle';
    portability: 'cross_tenant_project';
    root_definition_ref: string;
    instance_transport: 'excluded' | 'target_recompile_recipe';
    assignment_transport: 'excluded' | 'pseudonymized_target_rebind';
  };
  organization_instances: readonly {
    instance_key: string;
    definition_ref: string;
    name: string;
    composition_mode: 'standard' | 'custom';
    team_count?: number;
    team_blueprint_counts?: Readonly<Record<string, number>>;
    requested_lifecycle: 'draft' | 'validated';
  }[];
  include_assignments: boolean;
  assignments: readonly {
    instance_key: string;
    unit_key: string;
    role_slot_key: string;
    principal_ref: string;
    principal_label?: string;
    redaction: 'pseudonymized';
  }[];
  [section: string]: unknown;
}

export interface OrganizationPlanningNode {
  id: string;
  kind: 'goal' | 'category_todo' | 'planning_track' | 'milestone' | 'task';
  label: string;
  status: string;
  revision?: string;
  digest?: string;
  parent_id?: string | null;
  source_category_item_ids?: readonly string[];
}

export interface WorkerTaskProposalView {
  proposal_id: string;
  revision: string;
  digest: string;
  source_task_id: string;
  proposer_role_slot_id: string;
  status: 'pending' | 'needs_approval' | 'accepted_as_plan_amendment' | 'rejected' | 'superseded';
  policy_hash: string;
  reason_code?: string;
  target_role_hint?: string;
  target_team_hint?: string;
  target_agent_hint?: string;
  selected_role_slot_id?: string;
  selected_team_id?: string;
  selected_agent_id?: string;
  approval_id?: string;
}

export interface OrganizationPlanningReadModel {
  organization_id: string;
  definition_revision: string;
  nodes: readonly OrganizationPlanningNode[];
  proposals: readonly WorkerTaskProposalView[];
  next_cursor: string | null;
}

export interface OrganizationLayoutPreference {
  node_id: string;
  x: number;
  y: number;
  collapsed?: boolean;
}
