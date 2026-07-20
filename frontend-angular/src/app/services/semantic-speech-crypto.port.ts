import { InjectionToken, inject } from '@angular/core';

import type { SemanticDataChannelMessage, SemanticTrafficClass } from './webrtc-datachannel.service';
import { SemanticSpeechCryptoService } from './semantic-speech-crypto.service';

export interface SemanticSpeechCryptoPort {
  seal(payload: Uint8Array, trafficClass: SemanticTrafficClass): Promise<SemanticDataChannelMessage>;
  open(message: SemanticDataChannelMessage): Promise<Uint8Array>;
}

export const SEMANTIC_SPEECH_CRYPTO = new InjectionToken<SemanticSpeechCryptoPort>(
  'SEMANTIC_SPEECH_CRYPTO',
  { providedIn: 'root', factory: () => inject(SemanticSpeechCryptoService) },
);
