export interface CollaborationWorkspaceSummary {
  schema: 'ananta.collaboration-workspace.v1';
  tenant_id: string;
  workspace_id: string;
  project_id: string | null;
  title: string;
  state: 'active';
  retention: 'standard' | 'audit' | 'legal_hold';
  revision: number;
  created_by: string;
  created_at: number;
  native_core: true;
  bridge_required: false;
  human_intervention_required: false;
  current_actor_binding_id?: string;
}

export interface CollaborationRoom {
  schema: 'ananta.collaboration-room.v1';
  room_id: string;
  room_kind: 'project' | 'goal' | 'task' | 'branch' | 'incident' | 'pair_session' | 'freeform';
  title: string;
  binding_kind: string | null;
  binding_id: string | null;
  access_mode?: 'workspace' | 'restricted';
  access_revision?: number;
  lifecycle_state?: 'active' | 'archived';
  lifecycle_revision?: number;
  snapshot_digest?: string | null;
}

export interface CollaborationActor {
  actor_binding_id: string;
  actor_kind: 'human' | 'agent' | 'worker' | 'resource' | 'service' | 'external_actor';
  display_name: string;
  profile?: { provider: string; model: string; profile_revision: string };
}

export interface CollaborationMembership {
  actor_binding_id: string;
  role: string;
  status: 'active' | 'revoked';
  revision: number;
  effective_capabilities?: string[];
  actor: CollaborationActor;
}

export interface CollaborationPresence {
  actor_binding_id: string;
  actor: CollaborationActor;
  presence_state: 'online' | 'stale';
  expires_at: number | null;
  membership_authority: false;
  task_ready_authority: false;
}

export interface CollaborationResourceOffer {
  schema: 'ananta.collaboration-resource-offer.v1';
  offer_id: string;
  workspace_id: string;
  owner_actor_binding_id: string;
  resource_id: string;
  capability_category: 'compute' | 'model' | 'repository' | 'terminal' | 'tool';
  capacity_class: 'small' | 'medium' | 'large';
  scopes: string[];
  expires_at: number;
  sensitivity: 'workspace' | 'restricted';
  attestation_status: 'verified' | 'unverified' | 'test_only';
  metadata: Record<string, unknown>;
  payload_digest?: string;
}

export interface CollaborationFlowProjection {
  schema: 'ananta.collaboration-flow-projection.v1';
  workspace_id: string;
  checkpoint: number;
  state: {
    tasks: Record<string, Record<string, unknown>>;
    workflows: Record<string, Record<string, unknown>>;
    git_refs: Record<string, Record<string, unknown>>;
    reviews: Record<string, Record<string, unknown>>;
    artifacts: Record<string, Record<string, unknown>>;
  };
  state_digest: string;
  writes_authoritative_state: false;
  worker_invoked: false;
}

export interface CollaborationEvent {
  event_id: string;
  workspace_id: string;
  room_id: string | null;
  thread_id: string | null;
  event_type: string;
  actor_binding_id: string;
  sequence: number;
  payload: Record<string, unknown>;
  human_intervention_required?: false;
  payload_digest?: string;
  source_refs?: string[];
  run_refs?: string[];
}

export interface CollaborationThread {
  thread_id: string;
  room_id: string;
  status: 'open' | 'resolved' | 'tombstoned';
  revision: number;
  events: CollaborationEvent[];
}

export interface CollaborationPage<T> {
  items: T[];
  limit: number;
  next_after?: number;
}
