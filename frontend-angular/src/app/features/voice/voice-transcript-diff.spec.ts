import { voiceTranscriptDiff } from './voice-transcript-diff';

describe('voiceTranscriptDiff', () => {
  it('keeps unchanged context while marking removed and inserted transcript words', () => {
    expect(voiceTranscriptDiff('hallo weld', 'Hallo Welt')).toEqual([
      { operation: 'insert', text: 'Hallo Welt' },
      { operation: 'delete', text: 'hallo weld' },
    ]);
  });

  it('collapses adjacent words with the same operation', () => {
    expect(voiceTranscriptDiff('das ist ananta', 'das ist das Ananta System')).toEqual([
      { operation: 'equal', text: 'das ist' },
      { operation: 'insert', text: 'das Ananta System' },
      { operation: 'delete', text: 'ananta' },
    ]);
  });
});
