export type PersonaOwnerKind = 'organization' | 'team' | 'agent';
export type PersonaSelectionState = 'missing' | 'inherit' | 'disabled' | 'asset';
export type PersonaMediaKind = 'image' | 'voice' | 'video' | 'style';
export interface PersonaAssetReference<K extends PersonaMediaKind = PersonaMediaKind> {
  tenant_id: string;
  project_id: string;
  artifact_id: string;
  revision: number;
  sha256: string;
  kind: K;
  classification: 'production' | 'synthetic' | 'test_only';
}
export type PersonaImageReference = PersonaAssetReference<'image'>;
export type PersonaVideoReference = PersonaAssetReference<'video'>;
export type PersonaVoiceReference = PersonaAssetReference<'voice'>;
export type PersonaStoredAssetKind = 'image' | 'video' | 'voice';
export interface PersonaAssetPage<K extends PersonaStoredAssetKind> {
  items: readonly PersonaAssetReference<K>[];
  next_cursor: string | null;
  purpose: 'preview';
}
export interface PersonaSelection<K extends PersonaMediaKind = PersonaMediaKind> {
  state: PersonaSelectionState;
  asset: PersonaAssetReference<K> | null;
}
export interface PersonaProfile {
  schema_version: 'ananta.persona-media.v1';
  tenant_id: string;
  project_id: string;
  owner_kind: PersonaOwnerKind;
  owner_id: string;
  persona_id: string;
  revision: number;
  image: PersonaSelection<'image'>;
  voice: PersonaSelection<'voice'>;
  video: PersonaSelection<'video'>;
  style: PersonaSelection<'style'>;
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
  selection: { organization_id: string; owner_kind: PersonaOwnerKind; owner_id: string; selection_digest: string };
  media: readonly {
    kind: 'image' | 'voice' | 'video' | 'style';
    state: PersonaSelectionState;
    asset: PersonaAssetReference | null;
    available: boolean;
    preview_allowed: boolean;
    publication_checked: false;
    origins: readonly { owner_kind: PersonaOwnerKind; owner_id: string; persona_id: string; profile_revision: number; selection_state: PersonaSelectionState }[];
  }[];
}
