import { Injectable, inject } from '@angular/core';

import {
  PairMediaE2eeCoordinatorService,
  type PublicPairMediaE2eeState,
} from './pair-media-e2ee-coordinator.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';

export const PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE = 'public_ordinary_media_e2ee_unavailable';
export const PUBLIC_ORDINARY_MEDIA_E2EE_NOT_READY = 'public_ordinary_media_e2ee_not_ready';

/** Separates Public media activation admission from key-bound capture readiness. */
@Injectable({ providedIn: 'root' })
export class PairOrdinaryMediaPolicy {
  private readonly controlPlane = inject(PairSessionControlPlaneService);
  private readonly coordinator = inject(PairMediaE2eeCoordinatorService);

  canActivate(sessionId: string): boolean {
    const authority = this.authority(sessionId);
    if (authority === 'hub') return true;
    if (authority !== 'public') return false;
    try {
      const status = this.coordinator.statusFor(sessionId);
      if (status.sessionId !== sessionId) return false;
      return status.state === 'ready' || this.coordinator.canActivate(sessionId);
    } catch { return false; }
  }

  assertActivationAllowed(sessionId: string): void {
    const authority = this.requireAuthority(sessionId);
    if (authority === 'hub') return;
    const status = this.coordinator.statusFor(sessionId);
    if (status.sessionId !== sessionId) throw new Error('public_ordinary_media_e2ee_context_mismatch');
    if (status.state === 'ready' || this.coordinator.canActivate(sessionId)) return;
    throw new Error(publicMediaReason(status));
  }

  allows(sessionId: string): boolean {
    const authority = this.authority(sessionId);
    if (authority === 'hub') return true;
    if (authority !== 'public') return false;
    try {
      const status = this.coordinator.statusFor(sessionId);
      return status.sessionId === sessionId && status.state === 'ready';
    } catch { return false; }
  }

  assertAllowed(sessionId: string): void {
    const authority = this.requireAuthority(sessionId);
    if (authority === 'hub') return;
    const status = this.coordinator.statusFor(sessionId);
    if (status.sessionId !== sessionId) throw new Error('public_ordinary_media_e2ee_context_mismatch');
    if (status.state !== 'ready') throw new Error(publicMediaReason(status));
  }

  private authority(sessionId: string): 'hub' | 'public' | null {
    if (!sessionId) return null;
    try { return this.controlPlane.authorityKindForSession(sessionId); } catch { return null; }
  }

  private requireAuthority(sessionId: string): 'hub' | 'public' {
    if (!sessionId) throw new Error('ordinary_media_session_missing');
    const authority = this.authority(sessionId);
    if (!authority) throw new Error('ordinary_media_session_binding_missing');
    return authority;
  }
}

function publicMediaReason(status: PublicPairMediaE2eeState): string {
  if (status.reasonCode) return status.reasonCode;
  return ({
    'awaiting-security': 'public_ordinary_media_e2ee_awaiting_security',
    'awaiting-peer': 'public_ordinary_media_e2ee_awaiting_peer',
    negotiating: 'public_ordinary_media_e2ee_negotiating',
    failed: PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE,
  } as Partial<Record<PublicPairMediaE2eeState['state'], string>>)[status.state]
    ?? PUBLIC_ORDINARY_MEDIA_E2EE_NOT_READY;
}
