import type { PersonaEffectiveProfile, PersonaProfileScope } from '../organizations/persona-media/persona-profile.models';
import { MeetVoiceSelection, validateVoiceSelection } from './meet-voice-selection';

/** Voice metadata is independent of images/video and never a publication grant. */
export function voiceCandidate(value: PersonaEffectiveProfile, scope: PersonaProfileScope): MeetVoiceSelection {
  const pin = value?.selection;
  if (value?.purpose !== 'preview' || value.runtime_bound !== false || !Array.isArray(value.media)
    || value.media.length !== 4 || new Set(value.media.map(row => row.kind)).size !== 4
    || value.media.some(row => !['image', 'video', 'voice', 'style'].includes(row.kind))
    || pin?.organization_id !== scope.organization || pin.owner_kind !== scope.kind || pin.owner_id !== scope.owner) {
    throw new Error('meet_voice_profile_invalid');
  }
  const voice = value.media.find(row => row.kind === 'voice');
  if (!voice?.asset || voice.asset.kind !== 'voice' || voice.state !== 'asset' || voice.available !== true || voice.preview_allowed !== true
    || voice.publication_checked !== false || voice.asset.project_id !== scope.project) throw new Error('meet_voice_profile_unavailable');
  return validateVoiceSelection({ mode: 'persona-voice-v1', reference: { ...voice.asset, kind: 'voice' }, profile: { ...pin } });
}
