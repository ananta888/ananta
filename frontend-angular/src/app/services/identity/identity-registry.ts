import { Injectable, inject, OnDestroy } from '@angular/core';
import { BehaviorSubject, Observable, combineLatest, Subscription } from 'rxjs';
import { map } from 'rxjs/operators';
import type { IdentitySnapshot, IdentitySphere, IdentitySource } from './identity.types';
import { HubIdentitySource } from './hub-identity-source';
import { OidcIdentitySource } from './oidc-identity-source';
import { UserAuthService } from '../user-auth.service';
import { WebrtcSignalingService } from '../webrtc-signaling.service';

/**
 * IdentityRegistry — owns the three IdentitySources (hub, oidc, signaling-derivation)
 * and exposes a unified status observable that downstream services can subscribe to.
 *
 * WebRTC follows only the OIDC sphere. Hub logout must not tear down an
 * otherwise valid Pair/WebRTC session.
 *
 * Single source of truth for "is the user authenticated at all?":
 *   registry.isAuthenticated$  emits true iff hub.status==='ready' OR oidc.status==='ready'
 */
@Injectable({ providedIn: 'root' })
export class IdentityRegistry implements OnDestroy {
  readonly hub: HubIdentitySource;
  readonly oidc: OidcIdentitySource;
  /**
   * Signaling is derived: when oidc.status === 'ready', signaling is available
   * (the signaling server expects the OIDC nonce as auth). When oidc is absent
   * or expired, signaling is unavailable.
   */
  readonly signaling: IdentitySource;

  private readonly auth = inject(UserAuthService);
  private readonly signalingSvc = inject(WebrtcSignalingService);
  private subscriptions = new Subscription();
  private lastHubStatus: string = 'absent';
  private lastOidcStatus: string = 'absent';
  private readonly _isAuthenticated$ = new BehaviorSubject<boolean>(false);
  readonly isAuthenticated$: Observable<boolean> = this._isAuthenticated$.asObservable();

  constructor() {
    this.hub = inject(HubIdentitySource);
    this.oidc = inject(OidcIdentitySource);

    // Build the derived signaling source as a thin wrapper around OIDC.
    this.signaling = {
      sphere: 'signaling' as const,
      snapshot$: this.oidc.snapshot$.pipe(
        map((oidcSnap): IdentitySnapshot => {
          if (oidcSnap.status === 'ready' && oidcSnap.token) {
            return {
              status: 'ready',
              token: oidcSnap.token,
              subject: oidcSnap.subject,
              issuer: 'oidc',
              expiresAt: oidcSnap.expiresAt,
              refreshAfter: oidcSnap.refreshAfter,
            };
          }
          if (oidcSnap.status === 'authenticating') return { status: 'authenticating' };
          return { status: 'absent' };
        }),
      ),
      refresh: async () => {
        await this.oidc.refresh();
      },
      logout: () => {
        // No-op: signaling has no own lifecycle; it follows OIDC.
      },
    };

    // Hub and Pair identities are independent. Track Hub state, but do not
    // disconnect WebRTC when only the Hub session ends.
    this.subscriptions.add(
      this.hub.snapshot$.subscribe((snap) => {
        this.lastHubStatus = snap.status;
      }),
    );

    this.subscriptions.add(
      this.oidc.snapshot$.subscribe((snap) => {
        const next = snap.status;
        if (this.lastOidcStatus !== 'absent' && next === 'absent') {
          this.signalingSvc.hardDisconnect();
        }
        this.lastOidcStatus = next;
      }),
    );

    // Compute isAuthenticated$ from both snapshots
    this.subscriptions.add(
      combineLatest([this.hub.snapshot$, this.oidc.snapshot$])
        .pipe(map(([hub, oidc]) => hub.status === 'ready' || oidc.status === 'ready'))
        .subscribe((v) => this._isAuthenticated$.next(v)),
    );
  }

  /**
   * Initialize all sources from storage.
   * Called once at app startup, before first user-facing screen.
   */
  async restoreAllFromStorage(): Promise<void> {
    await this.hub.restoreFromStorage();
    await this.oidc.restoreFromStorage();
  }

  /**
   * True if the user is authenticated via EITHER hub OR oidc.
   * Used by IdentityGate.
   */
  get isAuthenticated(): boolean {
    return this.hub.current.status === 'ready' || this.oidc.current.status === 'ready';
  }

  /**
   * Snapshot of all spheres. Useful for debugging / status pages.
   */
  snapshotAll(): Record<IdentitySphere, IdentitySnapshot> {
    return {
      hub: this.hub.current,
      oidc: this.oidc.current,
      signaling: {
        status: this.oidc.current.status === 'ready' ? 'ready' : 'absent',
        token: this.oidc.current.token,
        subject: this.oidc.current.subject,
      },
    };
  }

  /**
   * Logout of all spheres and disconnect WebRTC.
   * Side-effect: clears all identity storage keys.
   */
  logoutAll(): void {
    this.hub.logout();
    this.oidc.logout();
    this.signalingSvc.hardDisconnect();
    this.auth.setTokens(null, null);
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }
}
