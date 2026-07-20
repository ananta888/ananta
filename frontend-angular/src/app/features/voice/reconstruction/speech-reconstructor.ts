export type SpeechReconstructionMode = 'personalized' | 'generic' | 'ordinary_audio' | 'unavailable';

export interface SpeechReconstructionInput {
  turnId: string;
  revision: number;
  text: string;
  authority: 'provisional' | 'final' | 'corrected';
  features?: readonly number[];
  deadlineAtMs: number;
  signal?: AbortSignal;
  ordinaryAudioAvailable?: boolean;
}

export interface ReconstructedSpeechAudio {
  readonly format: string;
  play(): Promise<void>;
  release(): void;
}

export interface SpeechReconstructionQuality {
  engine: string;
  score: number;
  featureCoverage: number;
  provisional: boolean;
}

export interface SpeechReconstructionResult {
  mode: SpeechReconstructionMode;
  reasonCode: string | null;
  turnId: string;
  revision: number;
  authoritativeText: string;
  audio: ReconstructedSpeechAudio | null;
  quality: SpeechReconstructionQuality | null;
}

/** Receiver-local execution port. It deliberately exposes no Hub or task API. */
export interface SpeechReconstructor {
  reconstruct(input: SpeechReconstructionInput): Promise<SpeechReconstructionResult>;
  supersede(turnId: string, revision: number): void;
  destroy(): Promise<void>;
}
