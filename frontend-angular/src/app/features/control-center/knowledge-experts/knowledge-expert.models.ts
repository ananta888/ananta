export interface KnowledgeExpertBankView {
  bank_id: string;
  generation_id: string;
  previous_generation_id?: string;
  status: string;
  expert_count: number;
  policy_digest: string;
  provenance?: { source_count: number; knowledge_unit_count: number };
}

export interface KnowledgeExpertControlSnapshot {
  schema: 'ananta.knowledge-expert-control-snapshot.v1';
  enabled: boolean;
  rollout_state: string;
  active_banks: KnowledgeExpertBankView[];
  candidate_banks: KnowledgeExpertBankView[];
  gates: Record<string, string>;
  fallback_mode: 'rag_only';
}

export interface KnowledgeExpertControlCommand {
  schema: 'ananta.knowledge-expert-control-command.v1';
  action: 'activate' | 'rollback' | 'revoke' | 'disable';
  bank_id: string;
  generation_id: string;
  expected_generation_id: string;
  reason: string;
  confirmed: true;
}
