export interface SpreadsheetCell {
  address: string;
  value: string | number | boolean | null;
  formula: Record<string, unknown> | null;
  style_ref: string | null;
  displayed_value?: string;
  formula_text?: string | null;
  formula_ast?: Record<string, unknown> | null;
}

export interface SpreadsheetSheet {
  sheet_id: string;
  name: string;
  hidden: boolean;
  cells: SpreadsheetCell[];
}

export interface WorkbookSnapshot {
  schema: 'ananta.spreadsheet-workbook-snapshot.v1' | 'ananta.spreadsheet-workbook-snapshot.v2';
  snapshot_id: string;
  document_version_id: string;
  sheets: SpreadsheetSheet[];
}

export interface SpreadsheetViewport {
  schema: 'ananta.spreadsheet-workbook-viewport.v1';
  snapshot_digest: string;
  sheet_id: string;
  range: { start: string; end: string };
  tile: { row: number; column: number; rows: number; columns: number };
  offset: number;
  limit: number;
  total: number;
  has_more: boolean;
  cells: SpreadsheetCell[];
  projection_digest: string;
  backend_cell_count: number;
  source_grounding_verified: false;
  human_intervention_required: false;
}

export interface SpreadsheetRangeSelection {
  document_id: string;
  document_version: number;
  snapshot_digest: string;
  sheet_id: string;
  start: string;
  end: string;
}

export interface SpreadsheetSourceArtifact {
  artifact_id: string;
  sha256: string;
  size_bytes: number;
  format: 'xlsx' | 'ods' | 'csv';
  media_type: string;
}

export interface SpreadsheetDocument {
  schema: 'ananta.spreadsheet-document-version.v1';
  document_id: string;
  title: string;
  version: number;
  snapshot: WorkbookSnapshot;
  snapshot_digest: string;
  state: 'published';
  source_artifact?: SpreadsheetSourceArtifact;
  published_artifact?: SpreadsheetSourceArtifact;
  unsupported_objects?: string[];
  engine?: string;
  engine_version?: string;
  production_fidelity?: boolean;
  source_grounding_verified: boolean;
  human_intervention_required: false;
}

export interface SpreadsheetStudioCapabilities {
  schema: 'ananta.spreadsheet-studio-capability.v1';
  available: boolean;
  state: 'available' | 'disabled';
  mode: string;
  automatic_promotion_enabled: boolean;
  supported_formats: string[];
  libreoffice_fidelity_verified: boolean;
  training_available: boolean;
  source_grounding_verified: boolean;
  reason_code?: string;
  executor: Record<string, unknown>;
  human_intervention_required: false;
}

export interface SpreadsheetProposalResult {
  proposal_id: string;
  proposal_digest: string;
  document_id: string;
  base_version: number;
  base_snapshot_digest: string;
  actions: Array<Record<string, unknown>>;
  state: 'promoted' | 'candidate_ready' | 'rejected';
  promoted_version: number | null;
  candidate_snapshot_digest: string;
  diff: Array<{ sheet_id: string; cell: string; before: SpreadsheetCell | null; after: SpreadsheetCell | null }>;
  reason_codes: string[];
  actual_diff: {
    total: number;
    has_more: boolean;
    diff_digest: string;
    items: Array<{
      kind: string;
      object_id: string;
      sheet_id: string | null;
      cell: string | null;
      change_kinds: string[];
      before: unknown;
      after: unknown;
      direct: boolean;
      action_ids: string[];
    }>;
  };
  validation: {
    passed: boolean;
    reason_codes: string[];
    results: Array<Record<string, unknown>>;
    bindings?: Record<string, string>;
  };
  production_fidelity: boolean;
  candidate_artifact?: SpreadsheetSourceArtifact;
  human_intervention_required: false;
}

export interface SpreadsheetProposalJob {
  schema: 'ananta.spreadsheet-execution-job.v1';
  job_id: string;
  proposal_id: string;
  document_id: string;
  proposal_digest: string;
  status: 'dispatch_pending' | 'queued' | 'leased' | 'claimed' | 'completed' | 'failed';
  queue_position: number | null;
  result?: SpreadsheetProposalResult | { reason_code: string };
  automatic_decision: true;
  human_intervention_required: false;
}

export interface SpreadsheetFeedbackEvent {
  event_id: string;
  proposal_id: string;
  record_digest: string;
  kind: 'accepted' | 'corrected' | 'rejected' | 'skipped' | 'unsafe';
  human_intervention_required: false;
}

export interface SpreadsheetPrivacyPreview {
  event_id: string;
  record: Record<string, unknown>;
  record_digest: string;
  purpose: 'spreadsheet_action_training';
  masking_version: string;
  human_intervention_required: false;
}

export interface SpreadsheetTrainingConsent {
  consent_id: string;
  feedback_id: string;
  record_digest: string;
  version: number;
  state: 'active' | 'revoked';
  expires_at: number;
  impact?: {
    state: string;
    dataset_ids: string[];
    training_jobs: Array<{ job_id: string; state: string; reason_code: string }>;
    adapters: Array<{ adapter_id: string; state: string; reason_code: string }>;
    automatic_reconciliation: boolean;
    human_intervention_required: false;
  };
  human_intervention_required: false;
}

export interface SpreadsheetDataset {
  dataset_id: string;
  dataset_digest: string;
  record_count: number;
  split_counts: Record<'train' | 'validation' | 'eval' | 'test', number>;
  split_lock?: {
    state: 'locked';
    split_lock_digest: string;
    distribution_warnings: string[];
    eval_test_membership_digest: string;
  };
  recipe_manifest?: {
    recipe_version: string;
    recipe_digest: string;
    consent_refs?: string[];
  };
  materialization?: {
    state: string;
    excluded_feedback_ids: string[];
    maximum_cells_per_record: number;
  };
  readiness: { dry_run_ready: boolean; training_ready: boolean; reason_codes: string[] };
  human_intervention_required: false;
}

export interface SpreadsheetInferenceProposal {
  document_id: string;
  expected_version: number;
  base_snapshot_digest: string;
  result: { actions: Array<Record<string, unknown>> };
  automatic_apply: false;
  human_intervention_required: false;
}

export interface SpreadsheetTrainingAdmission {
  spreadsheet_dataset_id: string;
  ml_intern_dataset_id: string;
  job: { id?: string; job_id?: string; state?: string } & Record<string, unknown>;
  replayed: boolean;
  task_family: 'spreadsheet_actions';
  human_intervention_required: false;
}
