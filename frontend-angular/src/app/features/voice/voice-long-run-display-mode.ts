export const VOICE_LONG_RUN_DISPLAY_MODE_SEGMENT = 'segment' as const;
export const VOICE_LONG_RUN_DISPLAY_MODE_LIVE = 'live' as const;

export const VOICE_LONG_RUN_DISPLAY_MODES = [
  VOICE_LONG_RUN_DISPLAY_MODE_SEGMENT,
  VOICE_LONG_RUN_DISPLAY_MODE_LIVE,
] as const;

export type VoiceLongRunDisplayMode = typeof VOICE_LONG_RUN_DISPLAY_MODES[number];

/**
 * Existing and unknown recovery records retain the privacy-preserving
 * segment-wise behavior unless live preview was selected explicitly.
 */
export const DEFAULT_VOICE_LONG_RUN_DISPLAY_MODE: VoiceLongRunDisplayMode =
  VOICE_LONG_RUN_DISPLAY_MODE_SEGMENT;

export function normalizeVoiceLongRunDisplayMode(value: unknown): VoiceLongRunDisplayMode {
  return value === VOICE_LONG_RUN_DISPLAY_MODE_LIVE
    ? VOICE_LONG_RUN_DISPLAY_MODE_LIVE
    : VOICE_LONG_RUN_DISPLAY_MODE_SEGMENT;
}
