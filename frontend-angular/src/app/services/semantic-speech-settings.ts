export interface SemanticSpeechSettings {
  readonly displayMode: 'live' | 'segment';
  readonly segmentDurationSeconds: number;
  readonly correctEachSegment: boolean;
  readonly paused: boolean;
  readonly ordinaryAudioOverride: boolean;
}

export const DEFAULT_SEMANTIC_SPEECH_SETTINGS: SemanticSpeechSettings = Object.freeze({
  displayMode: 'live',
  segmentDurationSeconds: 60,
  correctEachSegment: true,
  paused: false,
  ordinaryAudioOverride: false,
});
