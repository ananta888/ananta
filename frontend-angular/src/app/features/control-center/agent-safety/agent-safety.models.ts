export interface AgentSafetyAgentView {
  agent_id: string;
  sandbox_id: string;
  state: string;
}

export interface AgentSafetyRunView {
  run_id: string;
  project_id: string;
  mode: string;
  state: string;
  execution_allowed: boolean;
  policy_id: string;
  policy_revision: number;
  agents: AgentSafetyAgentView[];
}

export interface AgentSafetyEventView {
  event_id: string;
  event_type: string;
  severity: string;
  source: string;
  observed_at: string;
  event_digest: string;
}

export interface AgentSafetyIncidentView {
  bundle_id: string;
  run_id: string;
  event_count: number;
  bundle_digest: string;
  created_at: string;
}

export interface AgentSafetyOverview {
  runs: AgentSafetyRunView[];
  incidents: AgentSafetyIncidentView[];
  controls: Array<{ operation_id: string; run_id: string; action: string; state: string }>;
  events: AgentSafetyEventView[];
  metrics: {
    boundary_outcomes: Record<string, number>;
    boundary_classes: Record<string, number>;
    self_reports: number;
    external_observations: number;
    containment_receipt_failures: number;
    incident_count: number;
    open_critical_findings: number;
    incident_replay_coverage: number;
  };
  containment_available: boolean;
  human_intervention_required: false;
}
