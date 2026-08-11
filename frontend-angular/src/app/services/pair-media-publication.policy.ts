import { Injectable, inject } from '@angular/core';

import { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PublicPairMediaPublicationConsentService } from './public-pair-media-publication-consent.service';
import type { PublicPairMediaSlot } from './public-pair-media-security-contract';

/** Final local-publication admission; receive/E2EE readiness stays separate. */
@Injectable({ providedIn: 'root' })
export class PairMediaPublicationPolicy {
  private readonly controlPlane = inject(PairSessionControlPlaneService);
  private readonly technicalMedia = inject(PairOrdinaryMediaPolicy);
  private readonly consent = inject(PublicPairMediaPublicationConsentService);

  allows(sessionId: string, slot: PublicPairMediaSlot): boolean {
    try {
      this.assertAllowed(sessionId, slot);
      return true;
    } catch {
      return false;
    }
  }

  assertAllowed(sessionId: string, slot: PublicPairMediaSlot): void {
    if (!sessionId) throw new Error('ordinary_media_session_missing');
    const authority = this.authority(sessionId);
    // Preserve the existing Hub publication contract unchanged.
    this.technicalMedia.assertAllowed(sessionId);
    if (authority === 'public') this.consent.assertAllowed(sessionId, slot);
  }

  private authority(sessionId: string): 'hub' | 'public' {
    try {
      const authority = this.controlPlane.authorityKindForSession(sessionId);
      if (authority === 'hub' || authority === 'public') return authority;
    } catch { /* Project one stable boundary reason below. */ }
    throw new Error('ordinary_media_session_binding_missing');
  }
}
