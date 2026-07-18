import { VpRuntimeOverlay } from './visual-process-api.service';

export type CanvasHitTargetKind =
  | 'node'
  | 'node_port'
  | 'edge'
  | 'edge_condition'
  | 'canvas_region'
  | 'validation_badge'
  | 'runtime_badge'
  | 'palette_item';

export interface CanvasHitTarget {
  kind: CanvasHitTargetKind;
  entityId: string;
  graphId: string;
  role: string;
  stepId?: string;
  edgeId?: string;
  portDirection?: 'input' | 'output';
  portName?: string;
}

export type VpAssistantDetailLevel = 'preview' | 'selected' | 'conversation';

export type VpAssistantLocationKind =
  | 'node'
  | 'field'
  | 'edge'
  | 'canvas'
  | 'validation'
  | 'runtime'
  | 'palette_item';

export interface VpAssistantLocation {
  target_kind: VpAssistantLocationKind;
  graph_id: string;
  entity_id?: string;
  field_path?: string;
  role?: string;
}

/** Browser projection of the Hub's EditorContextEnvelope contract. */
export interface VpEditorContextPayload {
  contract_version: 'ananta.visual_process.editor_context.v1';
  graph_id: string;
  repository_revision: string;
  codecompass_manifest_hash: string;
  source_allowlist_version: string;
  prompt_version: string;
  graph_schema_version: string;
  node_registry_version: string;
  definition_revision: number;
  definition_hash: string;
  draft_hash: string;
  runtime_snapshot_hash?: string;
  editor_mode: 'editor' | 'ai_snake' | 'read_only';
  locale: string;
  location: VpAssistantLocation;
  graph_excerpt: Record<string, unknown>;
  effective_configuration: Record<string, unknown>;
  validation_issues: Array<Record<string, unknown>>;
  runtime_overlay?: VpRuntimeOverlay;
  evidence_refs: VpHelpEvidence[];
  allowed_mutations: string[];
  extensions: Record<string, unknown>;
}

export interface VpEditorContextEnvelope extends VpEditorContextPayload {
  context_id: string;
  detail_level: VpAssistantDetailLevel;
}

export interface VpHelpEvidence {
  evidence_id: string;
  source_id?: string;
  source_version?: string;
  tenant_id?: string;
  scope?: string;
  provenance_digest?: string;
  path?: string;
  line_start?: number;
  line_end?: number;
  trust_level?: 'extracted' | 'declared' | 'inferred' | 'manual';
  verification_status: 'verified' | 'unverified' | 'failed';
  excerpt?: string;
  reason_codes?: string[];
}

export interface VpAssistantClaim {
  claim_id: string;
  text: string;
  evidence_refs: string[];
  verification_status: 'verified' | 'unverified' | 'failed';
}

export interface VpWorkflowPatchOperation {
  operation_id: string;
  op: 'add_step' | 'remove_step' | 'update_step_field' | 'add_edge' | 'remove_edge' | 'update_edge_condition';
  step_id?: string;
  edge_id?: string;
  temp_id?: string;
  path?: string;
  value?: unknown;
  expected_old_value?: unknown;
  source?: string;
  target?: string;
  condition?: Record<string, unknown>;
  evidence_refs: string[];
}

export interface VpWorkflowPatch {
  contract_version: 'ananta.visual_process.workflow_patch.v1';
  graph_id: string;
  definition_revision: number;
  base_graph_hash: string;
  operations: VpWorkflowPatchOperation[];
  evidence_refs: string[];
  extensions: Record<string, unknown>;
}

export interface VpHelpResponse {
  contract_version?: 'ananta.visual_process.help_response.v1';
  summary: string;
  location: string | VpAssistantLocation;
  explanation: string;
  options: Array<string | Record<string, unknown>>;
  warnings: string[];
  next_actions: string[];
  evidence: VpHelpEvidence[];
  context_id?: string;
  prompt_version?: string;
  claims?: VpAssistantClaim[];
  workflow_patch?: VpWorkflowPatch | null;
  extensions?: Record<string, unknown>;
}
