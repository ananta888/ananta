export interface MemoryAuditFilters {
  run_id?: string;
  task_id?: string;
  trace_id?: string;
  channel_id?: string;
  scope?: string;
}

export interface MemoryAuditEvent {
  event_id: string;
  sequence: number;
  event_hash: string;
  previous_hash: string;
  created_at: number;
  retention_until: number;
  actor_subject: string;
  run_id: string;
  trace_id: string | null;
  operation: string;
  allowed: boolean;
  rule_id: string;
  reason_code: string;
  policy_revision: string | null;
  policy_digest: string | null;
  channel_id: string;
  namespace: Record<string, string | null>;
  security: { scope: string; sensitivity: string; retention_class: string; trust_level: string };
  event_kind: string;
}

export interface MemoryAuditPage {
  schema: 'ananta.memory-audit-page.v1';
  project_id: string;
  items: MemoryAuditEvent[];
  next_after_sequence: number | null;
  integrity: 'event_digests_verified';
  complete_chain_verified: false;
}

export interface MemorySnapshotEntry {
  entry_id: string;
  content_digest: string;
  metadata_digest: string;
  security: MemoryAuditEvent['security'];
}

export interface MemoryDiff {
  run_id: string;
  channel_id: string;
  scope: string;
  status: 'complete' | 'incomplete';
  scope_outcome?: 'completed' | 'failed';
  attribution: 'observed_scope_change';
  before_snapshot_id: string;
  after_snapshot_id: string | null;
  audit_event_ids: string[];
  changes: { entry_id: string; kind: 'added' | 'modified' | 'deleted';
    before: MemorySnapshotEntry | null; after: MemorySnapshotEntry | null }[];
}

export interface MemoryDiffPage {
  schema: 'ananta.memory-diff-page.v1';
  project_id: string;
  run_id: string;
  integrity: 'snapshot_and_audit_links_verified';
  items: MemoryDiff[];
  production_release_evidence: false;
}
