import type { PersonaEffectiveProfile, PersonaImageReference } from '../organizations/persona-media/persona-profile.models';

export type MeetAvatarSelection = { mode: 'neutral-ai-v1' } | {
  mode: 'persona-image-v1'; reference: PersonaImageReference; profile: PersonaEffectiveProfile['selection'];
};
const id = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,160}$/.test(value);
const digest = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export function validateAvatarSelection(value: MeetAvatarSelection): MeetAvatarSelection {
  if (value?.mode === 'neutral-ai-v1' && Object.keys(value).join() === 'mode') return value;
  if (value?.mode !== 'persona-image-v1' || Object.keys(value).sort().join() !== 'mode,profile,reference') {
    throw new Error('meet_avatar_selection_invalid');
  }
  const { reference, profile } = value;
  if (!reference || Object.keys(reference).sort().join() !== 'artifact_id,classification,kind,project_id,revision,sha256,tenant_id'
    || ![reference.tenant_id, reference.project_id, reference.artifact_id].every(id)
    || reference.kind !== 'image' || reference.revision !== 1 || !digest(reference.sha256)
    || !['production', 'synthetic', 'test_only'].includes(reference.classification)
    || !profile || Object.keys(profile).sort().join() !== 'organization_id,owner_id,owner_kind,selection_digest'
    || !id(profile.organization_id) || !id(profile.owner_id) || !digest(profile.selection_digest)
    || !['organization', 'team', 'agent'].includes(profile.owner_kind)) throw new Error('meet_avatar_selection_invalid');
  return value;
}
