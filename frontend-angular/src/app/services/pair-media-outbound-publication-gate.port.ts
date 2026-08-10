import { InjectionToken, inject } from '@angular/core';

import {
  PairMediaE2eeTransformAdapter,
  type PublicPairMediaOutboundPublicationGate,
} from './pair-media-e2ee-transform.adapter';

/** Narrow port used by local consent; crypto/worker details stay behind it. */
export interface PairMediaOutboundPublicationGatePort {
  setOutboundPublicationGate(
    sessionId: string,
    adapterGeneration: number,
    gate: Readonly<PublicPairMediaOutboundPublicationGate>,
  ): Promise<void>;
}

/** Composition-root binding for the Public Pair encoded-transform adapter. */
export const PAIR_MEDIA_OUTBOUND_PUBLICATION_GATE =
  new InjectionToken<PairMediaOutboundPublicationGatePort>(
    'PAIR_MEDIA_OUTBOUND_PUBLICATION_GATE',
    {
      providedIn: 'root',
      factory: () => inject(PairMediaE2eeTransformAdapter),
    },
  );
