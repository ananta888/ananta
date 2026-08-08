import { Injectable, inject } from '@angular/core';

import { PairSessionControlPlaneService } from './pair-session-control-plane.service';

export const PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE = 'public_ordinary_media_e2ee_unavailable';

/** Blocks ordinary audio/video until Public Pair has a key-bound media transform. */
@Injectable({ providedIn: 'root' })
export class PairOrdinaryMediaPolicy {
  private readonly controlPlane = inject(PairSessionControlPlaneService);

  allows(sessionId: string): boolean {
    if (!sessionId) return false;
    try { return this.controlPlane.authorityKindForSession(sessionId) === 'hub'; } catch { return false; }
  }

  assertAllowed(sessionId: string): void {
    if (!sessionId) throw new Error('ordinary_media_session_missing');
    let authority: 'hub' | 'public';
    try { authority = this.controlPlane.authorityKindForSession(sessionId); } catch {
      throw new Error('ordinary_media_session_binding_missing');
    }
    if (authority !== 'hub') throw new Error(PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
  }
}
