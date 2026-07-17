import {
  DEFAULT_VOICE_LONG_RUN_DISPLAY_MODE,
  VOICE_LONG_RUN_DISPLAY_MODE_LIVE,
  VOICE_LONG_RUN_DISPLAY_MODE_SEGMENT,
  VOICE_LONG_RUN_DISPLAY_MODES,
  normalizeVoiceLongRunDisplayMode,
} from './voice-long-run-display-mode';

describe('normalizeVoiceLongRunDisplayMode', () => {
  it('preserves both canonical display modes', () => {
    expect(normalizeVoiceLongRunDisplayMode(VOICE_LONG_RUN_DISPLAY_MODE_SEGMENT))
      .toBe(VOICE_LONG_RUN_DISPLAY_MODE_SEGMENT);
    expect(normalizeVoiceLongRunDisplayMode(VOICE_LONG_RUN_DISPLAY_MODE_LIVE))
      .toBe(VOICE_LONG_RUN_DISPLAY_MODE_LIVE);
    expect(VOICE_LONG_RUN_DISPLAY_MODES).toEqual(['segment', 'live']);
  });

  it.each([
    undefined,
    null,
    '',
    'legacy',
    'LIVE',
    1,
    false,
    {},
  ])('normalizes an unknown or legacy value %j to the safe default', (value) => {
    expect(normalizeVoiceLongRunDisplayMode(value)).toBe(DEFAULT_VOICE_LONG_RUN_DISPLAY_MODE);
  });
});
