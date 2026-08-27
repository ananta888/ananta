export type DatasetFormat = 'instruction' | 'chat' | 'mixed' | 'unknown';
export type DatasetSplit = 'train' | 'validation';
export type DatasetStatus = 'uploaded' | 'split' | 'validating' | 'valid' | 'invalid' | 'failed' | string;

export interface TrainingPage<T> {
  items: T[];
  count: number;
  next_cursor?: string | null;
}

export interface DatasetSummary {
  id: string;
  name: string;
  purpose?: string;
  license?: string;
  privacy?: string;
  format: DatasetFormat;
  status: DatasetStatus;
  sha256?: string;
  size_bytes: number;
  record_count: number;
  accepted_record_count?: number;
  rejected_record_count?: number;
  duplicate_record_count?: number;
  train_record_count: number;
  validation_record_count: number;
  validation_status?: string;
  trainable?: boolean;
  created_at?: number;
  updated_at?: number;
}

export interface DatasetRecord {
  id?: string;
  index: number;
  split: DatasetSplit;
  format?: DatasetFormat;
  instruction?: string;
  input?: string;
  output?: string;
  messages?: Array<{ role: string; content: string }>;
  token_count?: number;
  valid?: boolean;
  reason_codes?: string[];
}

export interface DatasetValidationIssue {
  code: string;
  severity: 'error' | 'warning' | 'info' | string;
  count?: number;
  record_index?: number;
  field?: string;
  message?: string;
  redacted?: boolean;
}

export interface DatasetValidationReport {
  dataset_id: string;
  valid: boolean;
  trainable?: boolean;
  format?: DatasetFormat;
  total_records: number;
  accepted_records: number;
  rejected_records: number;
  duplicate_records: number;
  secret_findings: number;
  pii_findings?: number;
  train_records?: number;
  validation_records?: number;
  issues: DatasetValidationIssue[];
  generated_at?: number;
}

export interface DatasetDetail extends DatasetSummary {
  split?: {
    algorithm_version?: string;
    seed: number;
    validation_ratio: number;
    train_count: number;
    validation_count: number;
  };
  external_validation?: {
    dataset_id: string;
    semantic_overlap_count: number;
    algorithm_version: string;
  };
  validation_report?: DatasetValidationReport | null;
}

export interface AttachValidationDatasetRequest {
  validation_dataset_id: string;
}

export interface DatasetDeletionResult {
  id: string;
  deleted: true;
}

export interface DatasetUploadInput {
  file: File;
  name?: string;
  purpose: string;
  license: string;
  privacy: string;
  validation_ratio: number;
  split_seed: number;
}

export type DatasetUploadEvent =
  | { kind: 'progress'; loaded: number; total?: number; percent?: number }
  | { kind: 'complete'; dataset: DatasetDetail };

export interface DatasetListFilters {
  status?: string;
  format?: string;
  q?: string;
  cursor?: string;
  limit?: number;
}

export interface TrainingBackendCapability {
  id: string;
  available: boolean;
  reason_code?: string;
  version?: string;
  license_spdx?: string;
  maintenance?: 'active' | 'unmaintained' | string;
  maturity?: 'experimental' | 'production' | string;
  methods?: string[];
  objectives?: string[];
  resource_profiles?: string[];
}

export interface TrainingBackendRecommendationRequest {
  objective: 'sft';
  method: 'lora' | 'qlora';
  modality: 'text';
  resource_profile: string;
  estimated_model_bytes: number;
  runtime_budget_seconds: number;
  export_format: 'adapter';
}

export interface TrainingBackendRecommendation {
  schema_version: string;
  mode: 'manual' | 'recommendation';
  backend: string;
  requires_confirmation: boolean;
  reasons: string[];
  capability_evidence: { source: string; backend_version: string; available: boolean; reason_code?: string };
  estimated_resources: { model_bytes: number; runtime_budget_seconds: number; profile: string; estimate_only: boolean };
  alternatives: Array<{ backend: string; maintenance: string; maturity: string }>;
  fallback_policy: 'new_visible_attempt_only';
}

export interface TrainingGpuProfile {
  id: string;
  label?: string;
  available: boolean;
  memory_bytes?: number;
  max_batch_size?: number;
  max_sequence_length?: number;
  reason_code?: string;
}

export interface TrainingBaseModel {
  id: string;
  label?: string;
  local: boolean;
  available?: boolean;
  compatible_backends: string[];
  reason_code?: string;
}

export interface TrainingCapabilities {
  available: boolean;
  mode?: TrainingMode;
  reason_code?: string;
  backends: TrainingBackendCapability[];
  gpu_profiles: TrainingGpuProfile[];
  base_models: TrainingBaseModel[];
  unsloth?: UnslothCapabilities;
  limits: {
    max_dataset_bytes?: number;
    max_adapter_bytes?: number;
    max_concurrent_jobs?: number;
    min_validation_ratio?: number;
    max_validation_ratio?: number;
    max_lora_rank?: number;
    max_lora_alpha?: number;
    max_batch_size?: number;
    max_gradient_accumulation_steps?: number;
    min_sequence_length?: number;
    max_sequence_length?: number;
    max_steps?: number;
    max_model_bytes?: number;
    max_checkpoint_bytes?: number;
    max_export_bytes?: number;
    max_tenant_storage_bytes?: number;
    storage_retention_seconds?: number;
    max_cleanup_items?: number;
  };
}

export type TrainingMode = 'dry_run' | 'live';
export type TrainingJobStatus =
  | 'queued'
  | 'claimed'
  | 'running'
  | 'cancel_requested'
  | 'cancelled'
  | 'completed'
  | 'failed'
  | 'interrupted'
  | string;

export interface TrainingHyperparameters {
  lora_rank: number;
  lora_alpha: number;
  lora_dropout: number;
  learning_rate: number;
  batch_size: number;
  gradient_accumulation_steps: number;
  max_steps: number;
  max_sequence_length: number;
  target_modules?: string[];
  quantization: 'none' | '4bit';
}

export interface CreateTrainingJobRequest {
  dataset_id: string;
  base_model_id: string;
  backend: string;
  mode: TrainingMode;
  gpu_profile: string;
  method: 'lora' | 'qlora';
  output_name: string;
  hyperparameters: TrainingHyperparameters;
  require_dataset_validation: true;
  require_secret_scan: true;
  risk_reason?: string;
  live_confirmed?: boolean;
}

export interface TrainingMetric {
  step: number;
  max_steps?: number;
  epoch?: number;
  train_loss?: number;
  eval_loss?: number;
  learning_rate?: number;
  gpu_memory_bytes?: number;
  recorded_at?: number;
}

export interface TrainingJobSummary {
  id: string;
  task_id?: string;
  status: TrainingJobStatus;
  phase?: string;
  dataset_id: string;
  dataset_name?: string;
  base_model_id: string;
  backend: string;
  mode?: TrainingMode;
  queue_position?: number | null;
  progress_percent?: number;
  current_step?: number;
  max_steps?: number;
  epoch?: number;
  latest_train_loss?: number;
  latest_eval_loss?: number;
  created_at?: number;
  started_at?: number;
  finished_at?: number;
}

export interface TrainingJobDetail extends TrainingJobSummary {
  configuration?: CreateTrainingJobRequest;
  metrics: TrainingMetric[];
  adapter_id?: string;
  artifact_refs?: string[];
  cancellable?: boolean;
  cancel_mode?: 'cooperative' | 'forced' | string;
  error?: { code: string; message: string; retriable?: boolean } | null;
}

export interface TrainingJobAcceptance {
  job_id: string;
  task_id?: string;
  status: string;
  poll_url?: string;
  events_url?: string;
  idempotent_replay?: boolean;
}

export interface TrainingJobEvent {
  sequence: number;
  event_type: string;
  phase?: string;
  progress_percent?: number;
  metric?: TrainingMetric;
  message?: string;
  reason_code?: string;
  timestamp?: number;
}

export interface TrainingEventPage {
  items: TrainingJobEvent[];
  count: number;
  next_sequence?: number;
}

export interface TrainingJobListFilters {
  status?: string;
  backend?: string;
  dataset_id?: string;
  cursor?: string;
  limit?: number;
}

export type AdapterStatus =
  | 'trained'
  | 'imported_pending_evaluation'
  | 'evaluated'
  | 'approved'
  | 'rejected'
  | 'deprecated'
  | string;

export interface AdapterSummary {
  id: string;
  name: string;
  version: number;
  adapter_version?: number | string;
  registry_version?: number;
  base_model_id: string;
  method?: string;
  status: AdapterStatus;
  score?: number;
  sha256?: string;
  size_bytes?: number;
  active?: boolean;
  hash_verified?: boolean;
  artifact_exists?: boolean;
  evaluation_id?: string;
  created_at?: number;
  updated_at?: number;
}

export interface AdapterImportInput {
  name: string;
  base_model_id: string;
  method: 'lora' | 'qlora';
  bundle?: File | null;
  config?: File | null;
  weights?: File | null;
}

export interface AdapterExportResult {
  artifact_id: string;
  sha256: string;
  size_bytes?: number;
  download_url?: string;
}

export interface AdapterExportDownload {
  blob: Blob;
  filename: string;
  sha256?: string;
}

export interface EvaluationMetric {
  name: string;
  base_value: number;
  adapter_value: number;
  delta: number;
  higher_is_better?: boolean;
  threshold?: number;
  passed?: boolean;
}

export interface EvaluationSample {
  id?: string;
  record_index?: number;
  base_output: string;
  adapter_output: string;
  expected_output?: string;
  winner?: 'base' | 'adapter' | 'tie' | string;
}

export interface EvaluationReport {
  id: string;
  adapter_id: string;
  dataset_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | string;
  passed?: boolean;
  aggregate_score?: number;
  metrics: EvaluationMetric[];
  samples: EvaluationSample[];
  reason_code?: string;
  created_at?: number;
  finished_at?: number;
}

export type EvaluationScorerName = 'generic' | 'ananta_todo_json';

export interface AdapterDecisionRequest {
  reason: string;
  expected_version: number;
  confirmed: true;
}

export interface AdapterRuntimeManagementRequest {
  confirmed: true;
  reason: string;
  expected_version?: number;
}

export interface AdapterRuntimeUnloadResult {
  adapter_id: string;
  adapter_version?: number | string;
  status: string;
  reason_code?: string;
  retryable?: boolean;
}

export type AdapterRuntimeRollbackTarget =
  | { type: 'adapter'; adapter_id: string; version: number; status: string }
  | { type: 'base_model_only'; base_model: string };

export interface AdapterRuntimeRollbackResult {
  adapter_id: string;
  version: number;
  status: string;
  rollback_target: AdapterRuntimeRollbackTarget;
  cache_unload: AdapterRuntimeUnloadResult;
  policy_decision?: {
    policy_version?: string;
    decision?: string;
    reason_code?: string;
    unapproved_fallback_allowed?: boolean;
  };
}

export type UnslothModality = 'text' | 'vision' | 'audio' | 'embedding';

export type UnslothMutationOperation = 'export' | 'runtime_handoff' | 'mcp' | 'cleanup';

export interface UnslothCapabilityFacet {
  available?: boolean;
  executable?: boolean;
  reason_code?: string;
  version?: string;
}

export interface UnslothReleaseProfileCapability extends UnslothCapabilityFacet {
  name?: string;
}

export interface UnslothCapabilities {
  status?: string;
  core?: UnslothCapabilityFacet;
  studio?: UnslothCapabilityFacet;
  modalities?: Partial<Record<UnslothModality, UnslothCapabilityFacet>>;
  mcp?: UnslothCapabilityFacet;
  release_profile?: UnslothReleaseProfileCapability;
  operations?: Partial<Record<
    UnslothMutationOperation | 'studio' | 'multimodal',
    UnslothCapabilityFacet
  >>;
}

export interface UnslothStorageKindUsage {
  bytes: number;
  artifacts: number;
}

export interface UnslothStorageQuotas {
  dataset_bytes: number;
  model_bytes: number;
  checkpoint_bytes: number;
  export_bytes: number;
  tenant_total_bytes: number;
  retention_seconds: number;
  max_cleanup_items: number;
}

export interface UnslothStorageUsage {
  schema: string;
  catalog_revision: number;
  usage: Record<string, UnslothStorageKindUsage>;
  tenant_total_bytes: number;
  quotas: UnslothStorageQuotas;
  paths_exposed: false;
}

export interface UnslothStorageArtifact {
  artifact_id: string;
  storage_ref?: string;
  kind: string;
  job_id: string;
  attempt_id: string;
  sha256: string;
  size_bytes: number;
  created_at?: number;
  retention_until?: number;
  state: string;
  reference_kinds: string[];
  referenced: boolean;
  cleanup_task_id?: string;
}

export interface UnslothStorageReadModel {
  usage: UnslothStorageUsage;
  items: UnslothStorageArtifact[];
  count: number;
}

export interface UnslothStorageCleanupFields {
  artifact_ids: string[];
  expected_catalog_revision: number;
  retention_before?: number;
}

export interface UnslothRuntimeApiCapabilities {
  openai_chat: boolean;
  openai_responses: boolean;
  anthropic_messages: boolean;
  streaming: boolean;
  tools: boolean;
  structured_output: boolean;
}

export interface UnslothRuntimeLimits {
  timeout_seconds: number;
  context_tokens: number;
  max_output_tokens: number;
  stream_idle_timeout_seconds: number;
}

export interface UnslothRuntimeProviderDescriptor {
  provider_id: string;
  provider_type: string;
  model_id: string;
  provider_revision: string;
  capabilities: UnslothRuntimeApiCapabilities;
  limits: UnslothRuntimeLimits;
}

export interface UnslothRuntimeEndpointDescriptor {
  endpoint_id: string;
  display_name: string;
  routing_key: string;
}

export interface UnslothRuntimeHandoffCommandFields {
  promoted_artifact_id: string;
  promoted_artifact_sha256: string;
  provider_descriptor: UnslothRuntimeProviderDescriptor;
  endpoint_descriptor: UnslothRuntimeEndpointDescriptor;
  expected_endpoint_revision: number;
  source_ids: string[];
  run_ids: string[];
}

export interface UnslothMutationCommand {
  operation: UnslothMutationOperation;
  resource_id: string;
  reason: string;
  dry_run: boolean;
  confirmed: boolean;
  confirmation_id?: string;
  promoted_artifact_id?: string;
  promoted_artifact_sha256?: string;
  provider_descriptor?: UnslothRuntimeProviderDescriptor;
  endpoint_descriptor?: UnslothRuntimeEndpointDescriptor;
  expected_endpoint_revision?: number;
  source_ids?: string[];
  run_ids?: string[];
  artifact_ids?: string[];
  expected_catalog_revision?: number;
  retention_before?: number;
}

export interface UnslothMutationResult {
  accepted: boolean;
  operation: UnslothMutationOperation;
  resource_id?: string;
  dry_run?: boolean;
  reason_code?: string;
  message?: string;
  confirmation_id?: string;
  replayed?: boolean;
  summary?: Record<string, unknown>;
}

export type ModelTrainingTab = 'datasets' | 'training' | 'jobs' | 'adapters';
