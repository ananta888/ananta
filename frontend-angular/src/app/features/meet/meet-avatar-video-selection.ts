import type { PersonaEffectiveProfile, PersonaProfileScope, PersonaVideoReference } from '../organizations/persona-media/persona-profile.models';

export interface MeetAvatarVideoSelection {
  mode: 'persona-video-v1'; reference: PersonaVideoReference; profile: PersonaEffectiveProfile['selection'];
  repeat_mode: 'loop' | 'hold_last';
}
const id = (value: unknown) => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,160}$/.test(value);
const digest = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export function validateAvatarVideoSelection(value: MeetAvatarVideoSelection): MeetAvatarVideoSelection {
  if (value?.mode !== 'persona-video-v1' || Object.keys(value).sort().join() !== 'mode,profile,reference,repeat_mode'
    || !['loop', 'hold_last'].includes(value.repeat_mode)) throw new Error('meet_avatar_video_selection_invalid');
  const { reference, profile } = value;
  if (!reference || Object.keys(reference).sort().join() !== 'artifact_id,classification,kind,project_id,revision,sha256,tenant_id'
    || ![reference.tenant_id, reference.project_id, reference.artifact_id].every(id)
    || reference.kind !== 'video' || reference.revision !== 1 || !digest(reference.sha256)
    || !['production', 'synthetic', 'test_only'].includes(reference.classification)
    || !profile || Object.keys(profile).sort().join() !== 'organization_id,owner_id,owner_kind,selection_digest'
    || !id(profile.organization_id) || !id(profile.owner_id) || !digest(profile.selection_digest)
    || !['organization', 'team', 'agent'].includes(profile.owner_kind)) throw new Error('meet_avatar_video_selection_invalid');
  return value;
}

/** Passive candidate: voice/image outputs do not authorize or obstruct silent video. */
export function avatarVideoCandidate(value: PersonaEffectiveProfile, scope: PersonaProfileScope): MeetAvatarVideoSelection {
  const pin = value?.selection;
  if (value?.purpose !== 'preview' || value.runtime_bound !== false || !Array.isArray(value.media)
    || value.media.length !== 4 || new Set(value.media.map(row => row.kind)).size !== 4
    || value.media.some(row => !['image', 'video', 'voice', 'style'].includes(row.kind))
    || pin?.organization_id !== scope.organization || pin.owner_kind !== scope.kind || pin.owner_id !== scope.owner) {
    throw new Error('meet_avatar_video_profile_invalid');
  }
  const video = value.media.find(row => row.kind === 'video');
  if (!video?.asset || video.asset.kind !== 'video' || video.state !== 'asset' || video.available !== true
    || video.preview_allowed !== true || video.publication_checked !== false || video.asset.project_id !== scope.project) {
    throw new Error('meet_avatar_video_profile_unavailable');
  }
  return validateAvatarVideoSelection({ mode: 'persona-video-v1', reference: { ...video.asset, kind: 'video' },
    profile: { ...pin }, repeat_mode: 'hold_last' });
}
