export type KnowledgeCoverage = 'complete' | 'partial' | 'unknown';
export type KnowledgeConflictState = 'open' | 'pending_reingest' | 'resolved' | 'reopened';

export interface KnowledgeClaim {
  claim_id: string;
  project_id: string;
  revision: number;
  subject: string;
  predicate: string;
  value: unknown;
  source_id: string;
  source_revision: string;
  source_locator: string;
  coverage: KnowledgeCoverage;
  record_digest?: string;
}

export interface KnowledgeConflict {
  conflict_id: string;
  project_id: string;
  left_claim_id: string;
  left_claim_revision: number;
  left_claim_digest: string;
  right_claim_id: string;
  right_claim_revision: number;
  right_claim_digest: string;
  conflict_type: string;
  severity: string;
  evidence: string[];
  coverage: KnowledgeCoverage;
  state: KnowledgeConflictState;
  version: number;
  created_at: number;
  updated_at: number;
  basis_digest?: string;
}

export interface KnowledgeConflictDetail {
  conflict: KnowledgeConflict;
  left_claim: KnowledgeClaim | null;
  right_claim: KnowledgeClaim | null;
  timeline: Array<Record<string, unknown>>;
}

export interface CuratedWikiPage {
  page_id: string;
  project_id: string;
  slug: string;
  title: string;
  revision: number;
  body_markdown: string;
  claim_refs: Array<[string, number]>;
  conflict_refs: string[];
  source_refs: string[];
  aliases: string[];
  coverage: KnowledgeCoverage;
  content_hash: string;
}

export interface KnowledgeHealthSnapshot {
  snapshot_id: string;
  project_id: string;
  as_of: number;
  scope_version: string;
  coverage: KnowledgeCoverage;
  counts: Record<string, number | null>;
  oldest_open_age_seconds: number | null;
  trend: Record<string, number | null>;
  basis_digest: string;
}

export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ApiEnvelope<T> {
  status: 'ok' | 'error';
  data: T;
  message?: string;
}

export interface ConflictDecisionCommand {
  decision_id: string;
  expected_version: number;
  basis_digest: string;
  decision: 'keep_left' | 'keep_right' | 'keep_both' | 'request_correction' | 'dismiss_not_conflict';
  rationale: string;
  qualifiers: string[];
  writeback_requested: boolean;
  second_approver_id?: string;
}

export interface KnowledgeCorrectionDetail {
  proposal: {
    correction_id: string;
    conflict_id: string;
    source_id: string;
    source_revision: string;
    source_locator: string;
    proposal_digest: string;
  };
  state: string;
  three_way: {
    status: string;
    base_sha256: string;
    current_sha256: string;
    proposed_sha256: string;
    diff: string;
  };
}
