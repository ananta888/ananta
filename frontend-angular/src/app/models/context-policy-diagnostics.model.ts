import { PolicyLintResult } from './context-access-policy.model';

export interface ContextPolicyDiagnostics {
  active_policy_id: string;
  active_policy_version: number;
  lint_status: PolicyLintResult;
  has_default_policy_fallback: boolean;
  configured_cloud_workers: number;
  configured_external_workers: number;
  last_decision_error?: string;
  bypass_mode_active: boolean;
  degraded_mode_active: boolean;
  recent_denials: DenialSummary[];
  coverage_stats?: {
    total_requests: number;
    allowed_count: number;
    denied_count: number;
    redacted_count: number;
    summarized_count: number;
  };
}

export interface DenialSummary {
  reason_code: string;
  count: number;
  last_occurrence?: string;
  example_source_ref?: string;
}
