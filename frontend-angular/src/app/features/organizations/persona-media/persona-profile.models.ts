export type PersonaOwnerKind = 'organization' | 'team' | 'agent';
export type PersonaSelectionState = 'missing' | 'inherit' | 'disabled' | 'asset';
export interface PersonaImageReference {
  tenant_id: string;
  project_id: string;
  artifact_id: string;
  revision: number;
  sha256: string;
  kind: 'image';
  classification: 'production' | 'synthetic' | 'test_only';
}
export interface PersonaSelection {
  state: PersonaSelectionState;
  asset: PersonaImageReference | null;
}
export interface PersonaProfile {
  schema_version: 'ananta.persona-media.v1';
  tenant_id: string;
  project_id: string;
  owner_kind: PersonaOwnerKind;
  owner_id: string;
  persona_id: string;
  revision: number;
  image: PersonaSelection;
  voice: PersonaSelection;
  video: PersonaSelection;
  style: PersonaSelection;
  requested_usage: readonly string[];
}
export interface PersonaProfileSnapshot {
  profile: PersonaProfile | null;
  revision: number;
  content_hash: string | null;
  media_available: boolean;
  tenant_id: string;
}
export interface PersonaProfileScope {
  hub: string;
  project: string;
  organization: string;
  kind: PersonaOwnerKind;
  owner: string;
}
export interface PersonaEffectiveProfile {
  purpose: 'preview';
  runtime_bound: false;
  topology_revision: number;
  media: readonly {
    kind: 'image' | 'voice' | 'video' | 'style';
    state: PersonaSelectionState;
    asset: PersonaImageReference | null;
    available: boolean;
    preview_allowed: boolean;
    publication_checked: false;
    origins: readonly { owner_kind: PersonaOwnerKind; owner_id: string; persona_id: string; profile_revision: number; selection_state: PersonaSelectionState }[];
  }[];
}
