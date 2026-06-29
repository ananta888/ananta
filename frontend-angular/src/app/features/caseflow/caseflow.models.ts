export type CasePriority = 'critical' | 'high' | 'medium' | 'low';
export type CaseRisk = 'low' | 'medium' | 'high';

export interface CaseFlowCase {
  id: string;
  case_type: string;
  title: string;
  status: string;
  priority: CasePriority;
  risk: CaseRisk;
  owner?: string;
  created_at: string;
  updated_at: string;
  closed_at?: string;
  source?: string;
  domain_payload: Record<string, unknown>;
  metadata: Record<string, unknown>;
  is_deleted?: boolean;
}

export interface CaseEvent {
  id: string;
  case_id: string;
  event_type: string;
  actor_type: string;
  actor_id?: string;
  created_at: string;
  title: string;
  payload: Record<string, unknown>;
  trace_id?: string;
  artifact_id?: string;
}

export interface CaseArtifact {
  id: string;
  case_id: string;
  artifact_type: string;
  artifact_kind: string;
  title: string;
  source: string;
  content_text?: string;
  status: string;
  trace_id?: string;
  agent_run_id?: string;
  version: number;
  is_sensitive: boolean;
  created_at: string;
}

export interface CaseAction {
  id: string;
  case_id: string;
  action_type: string;
  title: string;
  description?: string;
  status: string;
  due_at?: string;
  priority: string;
  blocking: boolean;
  completed_at?: string;
  created_at: string;
}

export interface DiscoveryResult {
  id: string;
  run_id: string;
  result_type: string;
  title: string;
  source_url?: string;
  source_name: string;
  is_duplicate: boolean;
  ignored: boolean;
  converted_to_case_id?: string;
  normalized_payload: Record<string, unknown>;
}

export interface SearchProfile {
  id: string;
  profile_type: string;
  name: string;
  enabled: boolean;
  query_terms: string[];
  exclude_terms: string[];
  locations: string[];
  remote_policy?: string;
}
