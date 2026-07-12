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
  backend?: string;
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

export interface VoiceCapabilityStatus {
  available: boolean;
  provider: string;
  capabilities: string[];
  models: VoiceModelCapability[];
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
