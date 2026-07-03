// Models für Verifiable Transparency / Tracking Viewer (TRANS-010)

export type PolicyDecision = 'allowed' | 'denied' | 'pending' | 'expired';
export type EvidenceKind = 'ast_call' | 'ast_import' | 'test_reference' | 'doc_mention' | 'heuristic' | 'llm_assessment';
export type ProposalStatus = 'draft' | 'pending_approval' | 'approved' | 'rejected' | 'applied' | 'expired';
export type ProviderKind = 'codecompass' | 'augment_mcp' | 'auggie_cli' | 'auggie_interactive' | 'fake';

export interface ContextHitSummary {
  provider: ProviderKind;
  path: string;
  score: number;
  is_external: boolean;
  policy_status: string;
  truncated: boolean;
}

export interface ContextDiscardSummary {
  path: string;
  reason: string;  // 'denied_path' | 'low_score' | 'duplicate' | 'over_budget'
  provider: string;
}

export interface ContextTraceSummary {
  trace_id: string;
  query: string;
  provider: string;
  selected_count: number;
  discarded_count: number;
  budget_chars_used: number;
  budget_chars_limit: number;
  has_external_evidence: boolean;
  policy_decisions: string[];
  selected_items?: ContextHitSummary[];
  discarded_items?: ContextDiscardSummary[];
}

export interface DelegationTraceSummary {
  trace_id: string;
  goal_summary: string;
  chosen_worker_id: string;
  chosen_expert_id: string | null;
  selection_reason: string;
  tools_granted: string[];
  alternatives_considered: AlternativeWorker[];
}

export interface AlternativeWorker {
  worker_id: string;
  reason_not_chosen: string;
}

export interface ToolCallSummary {
  tool_call_id: string;
  tool_name: string;
  timestamp: string;
  policy_decision: PolicyDecision;
  status: 'success' | 'failed' | 'denied';
  duration_ms: number;
  redaction_applied: boolean;
}

export interface DiffProposalSummary {
  proposal_id: string;
  total_files: number;
  total_lines_added: number;
  total_lines_removed: number;
  risk_summary: 'low' | 'high' | 'critical';
  status: ProposalStatus;
  policy_check_passed: boolean | null;
  is_applicable: boolean;
}

export interface ApprovalSummary {
  gate_id: string;
  gate_type: string;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  status: 'pending' | 'approved' | 'denied' | 'expired';
  expires_at: number | null;
  decided_by: string | null;
}

export interface EvidenceSummary {
  evidence_type: EvidenceKind;
  source_file: string;
  confidence: number;
  description: string;
}

export interface RunStepTrace {
  step_id: string;
  step_name: string;
  expert_id: string | null;
  state: string;
  context_trace?: ContextTraceSummary;
  delegation_trace?: DelegationTraceSummary;
  tool_calls: ToolCallSummary[];
  diff_proposals: DiffProposalSummary[];
  approval_gates: ApprovalSummary[];
  evidence: EvidenceSummary[];
  model_claims: string[];        // what the model reported
  verified_facts: string[];      // what was independently verified
  local_only: boolean;
  policy_blockades: PolicyBlockade[];
  started_at: number;
  duration_ms: number | null;
}

export interface PolicyBlockade {
  action_attempted: string;
  blocked_reason: string;
  rule: string;
  severity: 'info' | 'warning' | 'hard_block';
}

export interface RunTransparencyReport {
  run_id: string;
  goal_id: string;
  steps: RunStepTrace[];
  overall_status: string;
  local_only_mode: boolean;
  has_external_providers: boolean;
  total_policy_blockades: number;
  verification_hash: string | null;
}
