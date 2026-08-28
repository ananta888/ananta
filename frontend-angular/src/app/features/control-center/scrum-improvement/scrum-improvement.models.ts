export interface ScrumSprintView {
  sprint_id: string;
  sequence: number;
  sprint_goal: string;
  lifecycle_state: string;
  architecture_handoff: { architecture_revision_id: string };
  improvement_commitment_ids: string[];
  scope_changes: unknown[];
}

export interface ArchitectureBaselineView {
  revision_id: string;
  lifecycle_state: string;
  parent_revision_id: string;
  guardrail_digest: string;
}

export interface ImprovementCommitmentView {
  commitment_id: string;
  status: string;
  owner_role: string;
  metric_names: string[];
}

export interface EffectView {
  evaluation_id: string;
  outcome: 'improved' | 'neutral' | 'regressed' | 'inconclusive';
  sprint_id?: string;
  commitment_id?: string;
  revision_id?: string;
}

export interface ScrumImprovementOverview {
  schema: 'ananta.scrum-continuous-improvement-overview.v1';
  scope_id: string;
  sprints: ScrumSprintView[];
  architecture_baselines: ArchitectureBaselineView[];
  improvement_commitments: ImprovementCommitmentView[];
  improvement_effects: EffectView[];
  architecture_effects: EffectView[];
  counts: {
    sprints: number;
    active_architecture_baselines: number;
    accepted_commitments: number;
    rolled_back_commitments: number;
  };
}
