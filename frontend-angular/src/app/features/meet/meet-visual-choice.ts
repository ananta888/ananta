export interface MeetStoredClipChoice {
  kind: 'stored_clip';
  artifactId: string;
  repeatMode: 'loop' | 'hold_last';
}

/** Closed UI choice only; the Hub still admits the artifact and publication. */
export function clipRequest(choice: MeetStoredClipChoice): { persona_video_id: string; video_repeat_mode: 'loop' | 'hold_last' } {
  if (!choice || typeof choice !== 'object'
    || Object.keys(choice).sort().join('|') !== 'artifactId|kind|repeatMode'
    || choice.kind !== 'stored_clip' || typeof choice.artifactId !== 'string'
    || !/^[A-Za-z0-9_.:-]{1,160}$/.test(choice.artifactId)
    || !['loop', 'hold_last'].includes(choice.repeatMode)) {
    throw new Error('meet_clip_selection_invalid');
  }
  return { persona_video_id: choice.artifactId, video_repeat_mode: choice.repeatMode };
}
