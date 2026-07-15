export type VoiceConfigurationScope = 'global' | 'profile' | 'session';
export type VoiceFieldType = 'boolean' | 'integer' | 'number' | 'string' | 'enum' | 'string_list';
export type VoiceReviewDecision = 'accept' | 'correct' | 'reject';
export type VoiceCandidateStatus = 'succeeded' | 'failed' | 'skipped' | 'cancelled';

export interface VoiceConfigurationOption {
  value: string | number | boolean;
  label?: string;
  enabled?: boolean;
  reason_code?: string | null;
}

export interface VoiceConfigurationField {
  key: string;
  label?: string;
  description?: string;
  group?: string;
  type: VoiceFieldType;
  default?: unknown;
  enum?: Array<string | number | boolean>;
  options?: VoiceConfigurationOption[];
  minimum?: number;
  maximum?: number;
  min_length?: number;
  max_length?: number;
  max_items?: number;
  unique_items?: boolean;
  scopes?: VoiceConfigurationScope[];
  visibility?: string;
  visible?: boolean;
  secret_reference?: boolean;
  admin_only?: boolean;
  required_capabilities?: string[];
  capability_reason_source?: string;
  enabled?: boolean;
  reason_code?: string | null;
}

export interface VoiceJsonSchemaProperty {
  type: 'boolean' | 'integer' | 'number' | 'string' | 'array' | 'object';
  enum?: Array<string | number | boolean>;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  uniqueItems?: boolean;
  maxItems?: number;
  items?: VoiceJsonSchemaProperty;
  properties?: Record<string, VoiceJsonSchemaProperty>;
  additionalProperties?: boolean;
  description?: string;
  scopes?: VoiceConfigurationScope[];
  visibility?: string;
  secret_reference?: boolean;
  required_capabilities?: string[];
  capability_reason_source?: string;
}

export interface VoiceConfigurationSchema {
  schema_version: 'ananta.voice-configuration.v1' | string;
  type?: 'object';
  additionalProperties?: boolean;
  properties?: Record<string, VoiceJsonSchemaProperty>;
  precedence?: string[];
  fields?: VoiceConfigurationField[];
  groups?: Array<{ id: string; label: string; description?: string }>;
}

export interface VoiceConfigurationSource {
  scope: VoiceConfigurationScope | 'default' | 'defaults' | 'legacy_global' | 'policy';
  scope_id?: string | null;
  version?: number | string;
  keys?: string[];
  delta?: Record<string, unknown>;
}

export interface VoiceConfigurationAdjustment {
  field: string;
  requested: string;
  effective: string;
  reason_code: string;
}

export interface VoiceConfiguration {
  schema_version: 'ananta.voice-configuration.v1' | string;
  effective: Record<string, unknown>;
  sources: VoiceConfigurationSource[] | Record<string, VoiceConfigurationSource>;
  version: string | number;
  adjustments?: VoiceConfigurationAdjustment[];
  delta?: Record<string, unknown>;
  scope?: VoiceConfigurationScope;
  scope_id?: string | null;
}

export interface VoiceConfigurationSaveResult {
  schema_version: 'ananta.voice-configuration.v1' | string;
  scope: VoiceConfigurationScope;
  scope_id: string;
  delta: Record<string, unknown>;
  version: number;
  idempotent_replay?: boolean;
}

export interface VoiceConfigurationQuery {
  profileId?: string;
  sessionId?: string;
}

export interface VoiceConfigurationMutation {
  scope: VoiceConfigurationScope;
  scope_id?: string;
  delta: Record<string, unknown>;
  expected_version?: number;
}

export interface VoiceResourceStatus {
  name?: string;
  device?: string;
  status?: string;
  available?: boolean;
  total_bytes?: number;
  free_bytes?: number;
  used_bytes?: number;
  memory_bytes?: number;
  reason_code?: string | null;
  [key: string]: unknown;
}

export interface VoiceModelCapability {
  id: string;
  provider?: string;
  display_name?: string;
  backend?: string;
  engine?: string;
  role?: string;
  purpose?: string;
  model_type?: string;
  revision?: string;
  device?: string;
  available?: boolean;
  local?: boolean;
  status?: string;
  capabilities?: string[];
  reason_code?: string | null;
  resources?: VoiceResourceStatus | VoiceResourceStatus[];
  [key: string]: unknown;
}

export interface VoiceCorrectionProviderCapability {
  id: string;
  display_name: string;
  available: boolean;
  supports_manual_model: boolean;
  reason_code?: string | null;
}

export interface VoiceCorrectionDefault {
  provider: string;
  model: string;
  configured_model?: string;
  source: string;
  available: boolean;
}

export interface VoiceCapabilityStatus {
  available: boolean;
  provider: string;
  capabilities: string[];
  models: VoiceModelCapability[];
  model_catalog?: VoiceModelCapability[];
  /** Optional Hub-provided subset. Older Hubs expose correctors in `models`. */
  correction_models?: VoiceModelCapability[];
  /** Additive provider-aware correction catalog. Missing on legacy Hubs. */
  correction_providers?: VoiceCorrectionProviderCapability[];
  /** Effective general LLM target used by the virtual `inherit` provider. */
  correction_default?: VoiceCorrectionDefault | null;
  limits?: { max_audio_mb?: number; [key: string]: unknown };
  privacy?: {
    store_audio_requested?: boolean;
    store_audio_effective?: boolean;
    effective_audio_retention?: string;
    policy_hint?: string;
    raw_audio_persisted?: boolean;
    [key: string]: unknown;
  };
  resources?: VoiceResourceStatus | VoiceResourceStatus[];
  routing_details?: {
    selected_backend?: string;
    selected_model_revision?: string;
    local?: boolean;
    device?: string;
    reasons?: Array<{ code: string; message?: string }>;
    skipped_backends?: Array<{ backend: string; reason_code: string }>;
    [key: string]: unknown;
  };
  health?: {
    ok?: boolean;
    status?: string;
    reason?: string;
    backend?: string;
    model_revision?: string;
    device?: string;
    local?: boolean;
    resources?: VoiceResourceStatus | VoiceResourceStatus[];
    skipped_backends?: Array<{ backend: string; reason_code: string }>;
    routing_reasons?: Array<{ code: string; message?: string }>;
    [key: string]: unknown;
  };
}

export interface VoiceWord {
  start_ms: number;
  end_ms: number;
  text: string;
  confidence?: number | null;
  candidate_id?: string | null;
}

export interface VoiceSegment {
  start_ms: number;
  end_ms: number;
  text: string;
  confidence?: number | null;
  speaker?: string | null;
  backend?: string | null;
  candidate_id?: string | null;
  words?: VoiceWord[];
  warnings?: string[];
}

export interface VoiceCandidateError {
  code: string;
  message: string;
  retriable?: boolean;
}

export interface VoiceCandidate {
  candidate_id: string;
  backend: string;
  model?: string | null;
  model_revision?: string | null;
  device?: string | null;
  execution_location?: string;
  manifest_digest?: string | null;
  synthetic?: boolean;
  audio_variant_id?: string;
  source_audio_digest?: string | null;
  lineage_id?: string | null;
  text: string;
  words?: VoiceWord[];
  segments?: VoiceSegment[];
  language?: string | null;
  duration_ms?: number | null;
  confidence?: number | null;
  latency_ms?: number | null;
  real_time_factor?: number | null;
  status: VoiceCandidateStatus;
  error?: VoiceCandidateError | null;
  warnings?: string[];
  provenance?: Record<string, unknown>;
  parent_candidate_ids?: string[];
}

export interface VoiceDisagreementAlternative {
  candidate_id: string;
  text: string;
  score?: number;
  backend?: string;
  lineage_id?: string;
  audio_variant_id?: string;
  source_audio_digest?: string;
  execution_location?: string;
}

export interface VoiceDisagreementRegion {
  region_id: string;
  start_ms?: number | null;
  end_ms?: number | null;
  alternatives: VoiceDisagreementAlternative[];
  selected_candidate_id?: string | null;
}

export interface VoiceTranscriptionResult {
  schema_version?: string;
  audit_id?: string;
  result_ref?: string;
  result_digest?: string;
  idempotent_replay?: boolean;
  text: string;
  /** Original ASR text before an optional, explicitly configured rewrite. */
  original_text?: string | null;
  language?: string | null;
  duration_ms?: number | null;
  model?: string | null;
  confidence?: number | null;
  raw_backend?: string | null;
  warnings?: string[];
  segments?: VoiceSegment[];
  candidates?: VoiceCandidate[];
  selected_candidate_id?: string | null;
  fusion_strategy?: string | null;
  disagreement_regions?: VoiceDisagreementRegion[];
  decision_trace?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
  provenance_valid?: boolean;
  /** Additive generative correction metadata. Kept tolerant for mixed Hub versions. */
  correction?: VoiceCorrectionMetadata | null;
  generative_corrector?: VoiceCorrectionMetadata | null;
}

export interface VoiceCorrectionEdit {
  operation?: 'insert' | 'delete' | 'replace' | 'equal' | string;
  before?: string | null;
  after?: string | null;
  source_text?: string | null;
  target_text?: string | null;
  start?: number | null;
  end?: number | null;
  reason?: string | null;
}

export interface VoiceCorrectionMetadata {
  original_text?: string | null;
  corrected_text?: string | null;
  proposed_text?: string | null;
  model_id?: string | null;
  model?: string | null;
  model_revision?: string | null;
  changed?: boolean;
  review_required?: boolean;
  edits?: VoiceCorrectionEdit[];
  warnings?: string[];
  [key: string]: unknown;
}

export type VoiceStreamStateName = 'created' | 'active' | 'finalizing' | 'final' | 'failed' | 'closed' | string;

export interface VoiceStreamState {
  session_id: string;
  state: VoiceStreamStateName;
  next_chunk_sequence: number;
  profile_id?: string;
  configuration_session_id?: string | null;
  task_id?: string | null;
  result_ref?: string | null;
  max_audio_seconds?: number;
  max_audio_bytes?: number;
  [key: string]: unknown;
}

export interface VoiceStreamEvent {
  event_type: string;
  payload?: {
    text?: string;
    stable_text?: string;
    finalized_text?: string;
    chunk_sequence?: number;
    next_chunk_sequence?: number;
    result?: VoiceTranscriptionResult;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface VoiceStreamCreateRequest {
  filename?: string;
  language?: string;
  profile_id: string;
  configuration_session_id?: string;
  media_type?: 'audio/pcm;rate=16000;channels=1' | string;
  deadline_seconds?: number;
  max_audio_seconds?: number;
}

export interface VoiceStreamCreateResponse {
  stream: VoiceStreamState;
  idempotent_replay?: boolean;
}

export interface VoiceStreamChunkResponse {
  stream: VoiceStreamState;
  event?: VoiceStreamEvent | null;
}

export interface VoiceStreamFinalizeResponse {
  stream: VoiceStreamState;
  result: VoiceTranscriptionResult;
  result_ref: string;
  event?: VoiceStreamEvent | null;
}

export interface VoiceStreamCancelResponse {
  stream: VoiceStreamState;
  deleted: boolean;
}

export type VoiceLongRunStatus =
  | 'created'
  | 'active'
  | 'finalizing'
  | 'completed'
  | 'completed_with_gaps'
  | 'expired'
  | 'failed'
  | 'cancelled'
  | string;

export type VoiceLongRunSegmentStatus =
  | 'pending'
  | 'processing'
  | 'completed'
  | 'failed'
  | string;

export interface VoiceLongRunSegment {
  sequence: number;
  status: VoiceLongRunSegmentStatus;
  started_at_ms?: number;
  ended_at_ms?: number;
  duration_ms?: number;
  overlap_milliseconds?: number | null;
  task_id?: string | null;
  result_ref?: string | null;
  text?: string | null;
  error?: VoiceCandidateError | null;
}

export interface VoiceLongRunResumeCursor {
  next_sequence: number;
  acknowledged_through_sequence?: number;
  next_started_at_ms?: number;
  last_seen_sequence?: number;
  pending_sequences?: number[];
  failed_sequences?: number[];
}

export interface VoiceLongRunState {
  id: string;
  parent_task_id?: string | null;
  status: VoiceLongRunStatus;
  profile_id?: string;
  configuration_session_id?: string | null;
  source?: 'microphone' | 'system_audio' | string;
  segment_duration_seconds?: number;
  max_duration_seconds?: number;
  overlap_milliseconds?: number;
  last_local_sequence?: number | null;
  expected_last_sequence?: number | null;
  started_at?: string | number | null;
  stopped_at?: string | number | null;
  capture_deadline_at?: string | number | null;
  expires_at?: string | number | null;
  final_result_ref?: string | null;
  stop_reason?: string | null;
}

export interface VoiceLongRunCreateRequest {
  source: 'microphone' | 'system_audio';
  profile_id: string;
  configuration_session_id?: string;
  language?: string;
  segment_duration_seconds: number;
  max_duration_seconds: number;
  overlap_milliseconds: number;
}

export interface VoiceLongRunLease {
  lease_token: string;
  expires_at: string | number;
  profile_id: string;
}

export type VoiceLongRunCreatePayload = VoiceLongRunCreateRequest & {
  lease_token: string;
};

export interface VoiceLongRunResponse {
  run: VoiceLongRunState;
  segments?: VoiceLongRunSegment[];
  composed_transcript?: string | null;
  gaps?: number[];
  resume?: VoiceLongRunResumeCursor;
  page?: {
    after_sequence: number;
    limit: number;
    has_more: boolean;
    next_after_sequence: number;
  };
  idempotent_replay?: boolean;
}

export interface VoiceLongRunSegmentUploadResponse extends VoiceLongRunResponse {
  segment: VoiceLongRunSegment;
  result?: VoiceTranscriptionResult & { transcript?: string };
  result_ref?: string;
  result_digest?: string;
}

export interface VoiceLongRunHeartbeatRequest {
  client_time_ms?: number;
  last_local_sequence: number;
  gaps?: number[];
}

export interface VoiceLongRunStopRequest {
  last_sequence?: number;
  reason?: string;
}

export interface VoiceReview {
  id: string;
  profile_id: string;
  session_id?: string | null;
  result_ref: string;
  candidate_ids: string[];
  state: 'pending' | 'accepted' | 'corrected' | 'rejected';
  selected_candidate_id?: string | null;
  correction_text?: string | null;
  version: number;
  created_at?: string | number;
  updated_at?: string | number;
  idempotent_replay?: boolean;
}

export interface VoiceConsent {
  id?: string | null;
  profile_id: string;
  granted: boolean;
  categories: VoiceConsentCategory[];
  retention_days?: number | null;
  version: number;
  granted_at?: string | number | null;
  revoked_at?: string | number | null;
  idempotent_replay?: boolean;
}

export type VoiceConsentCategory =
  | 'preferences'
  | 'text_corrections'
  | 'vocabulary'
  | 'audio_fingerprint';

export interface VoicePersonalizationItem {
  id: string;
  kind: 'vocabulary' | 'substitution' | 'preference' | 'negative';
  review_id?: string;
  source_review_id?: string;
  source_text?: string | null;
  target_text?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string | number;
}

export interface VoicePersonalizationImportItem {
  kind: 'vocabulary' | 'substitution' | 'preference' | 'negative';
  source_text?: string | null;
  target_text?: string | null;
  metadata?: Record<string, string>;
}

export interface VoicePersonalizationImportPayload {
  schema_version: 'voice-personalization.v1';
  profile_id: string;
  version?: number;
  items: VoicePersonalizationImportItem[];
}

export interface VoicePersonalizationImportResult {
  profile_id: string;
  imported_count: number;
  version: number;
  idempotent_replay?: boolean;
}

export interface VoicePersonalizationExport {
  schema_version: string;
  profile_id: string;
  version: number;
  items: VoicePersonalizationItem[];
}

export interface VoicePersonalizationSnapshot {
  schema_version: string;
  profile_id: string;
  version: number;
  consent_id: string;
  consent_version: number;
  expires_at: number;
  vocabulary: string[];
  substitutions: Array<{ source: string; target: string }>;
  preferences: Array<{ source: string; target: string }>;
  negative_examples: Array<{ source: string; target?: string | null }>;
  persistence_owner: 'hub';
  runtime_persistence_allowed: false;
}

export interface VoiceResetResult {
  profile_id: string;
  deleted_count: number;
  version: number;
  idempotent_replay?: boolean;
}

export interface VoicePrivacyDeletionResult {
  profile_id: string;
  deleted_count: number;
  deleted_by_store: Record<string, number>;
  snapshots_revoked: boolean;
  revoked_stream_count: number;
  runtime_cleanup_failed_count: number;
  runtime_cleanup_pending: boolean;
  idempotent_replay?: boolean;
}

export interface VoiceFineTuningExportTaskResult {
  task_id: string;
  idempotent_replay: boolean;
}

export interface VoiceApiError {
  code: string;
  message: string;
  retriable?: boolean;
}
