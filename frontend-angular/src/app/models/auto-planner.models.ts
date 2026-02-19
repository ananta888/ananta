/**
 * Frontend models for Auto-Planner
 */

export interface AutoPlannerStatus {
  enabled: boolean;
  auto_followup_enabled: boolean;
  max_subtasks_per_goal: number;
  default_priority: string;
  auto_start_autopilot: boolean;
  llm_timeout: number;
  llm_retry_attempts: number;
  llm_retry_backoff: number;
  stats: AutoPlannerStats;
}

export interface AutoPlannerStats {
  goals_processed: number;
  tasks_created: number;
  followups_created: number;
  errors: number;
  llm_retries?: number;
}

export interface AutoPlannerConfig {
  enabled?: boolean;
  auto_followup_enabled?: boolean;
  max_subtasks_per_goal?: number;
  default_priority?: string;
  auto_start_autopilot?: boolean;
  llm_timeout?: number;
  llm_retry_attempts?: number;
  llm_retry_backoff?: number;
}

export interface PlanGoalRequest {
  goal: string;
  context?: string;
  team_id?: string;
  parent_task_id?: string;
  create_tasks?: boolean;
  use_template?: boolean;
  use_repo_context?: boolean;
}

export interface PlanGoalResponse {
  subtasks: Subtask[];
  created_task_ids: string[];
  raw_response?: string;
  error?: string;
}

export interface Subtask {
  title: string;
  description?: string;
  priority?: string;
  depends_on?: string[];
}

export interface AnalyzeFollowupResponse {
  followups_created: Array<{
    id: string;
    title: string;
    priority: string;
  }>;
  analysis?: {
    task_complete: boolean;
    needs_review: boolean;
    suggestions?: string[];
  };
  skipped?: string;
  error?: string;
}

export interface TriggerStatus {
  enabled_sources: string[];
  configured_handlers: string[];
  webhook_secrets_configured: string[];
  ip_whitelists: Record<string, string[]>;
  rate_limits: Record<string, { max_requests: number; window_seconds: number }>;
  stats: TriggerStats;
  auto_start_planner: boolean;
}

export interface TriggerStats {
  webhooks_received: number;
  tasks_created: number;
  rejected: number;
  rate_limited: number;
  ip_blocked: number;
}

export interface TriggerConfig {
  enabled_sources?: string[];
  webhook_secrets?: Record<string, string>;
  ip_whitelists?: Record<string, string[]>;
  rate_limits?: Record<string, { max_requests: number; window_seconds: number }>;
  auto_start_planner?: boolean;
}

export interface TestTriggerResponse {
  source: string;
  parsed_tasks: Subtask[];
  would_create: number;
}
