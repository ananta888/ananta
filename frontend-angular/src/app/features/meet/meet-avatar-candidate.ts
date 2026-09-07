import type { PersonaEffectiveProfile, PersonaProfileScope } from '../organizations/persona-media/persona-profile.models';
import { MeetAvatarSelection, validateAvatarSelection } from './meet-avatar-selection';

/** Preview metadata is a candidate, never a grant or a running publication. */
export function avatarCandidate(value: PersonaEffectiveProfile, scope: PersonaProfileScope): MeetAvatarSelection {
  const pin = value?.selection;
  if (value?.purpose !== 'preview' || value.runtime_bound !== false || !Array.isArray(value.media)
    || value.media.length !== 4 || new Set(value.media.map(row => row.kind)).size !== 4
    || value.media.some(row => !['image', 'video', 'voice', 'style'].includes(row.kind))
    || pin?.organization_id !== scope.organization || pin.owner_kind !== scope.kind || pin.owner_id !== scope.owner) {
    throw new Error('meet_avatar_profile_invalid');
  }
  const image = value.media.find(row => row.kind === 'image');
  const video = value.media.find(row => row.kind === 'video');
  if (!image?.asset || image.asset.kind !== 'image' || image.state !== 'asset' || image.available !== true || image.preview_allowed !== true
    || image.publication_checked !== false || image.asset.project_id !== scope.project
    || !video || video.state !== 'missing') throw new Error('meet_avatar_profile_unavailable');
  // Voice is deliberately independent. The Hub alone checks actual publication.
  return validateAvatarSelection({ mode: 'persona-image-v1', reference: { ...image.asset, kind: 'image' }, profile: { ...pin } });
}
