export interface CollaborationWorkspaceSummary {
  schema: 'ananta.collaboration-workspace.v1';
  tenant_id: string;
  workspace_id: string;
  title: string;
  state: 'active';
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
}

export interface CollaborationPage<T> {
  items: T[];
  limit: number;
  next_after?: number;
}
