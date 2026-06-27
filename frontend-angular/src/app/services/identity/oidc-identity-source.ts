import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Observable, Subscription } from 'rxjs';
import type { IdentitySnapshot, IdentitySource } from './identity.types';
import {
  buildSnapshot,
  needsRefresh,
  snapshotFromJwt,
} from './identity-snapshot';
import { IDENTITY_STORAGE_LAYOUT } from './identity-storage-layout';
import { UserAuthService } from '../user-auth.service';
import { OidcAuthService } from '../oidc-auth.service';

/**
 * OidcIdentitySource — IdentitySource für die OIDC-Sphäre (Keycloak).
 *
 * Lifecycle:
 *   1. restoreFromStorage() — liest ananta.oidc.access_token aus localStorage
 *   2. snapshot$ — BehaviorSubject mit aktuellem Stand
 *   3. refresh() — delegiert an OidcAuthService.refreshFromStorage() (PKCE/refresh)
 *   4. logout() — OidcAuthService.logoutLocal() + clear all keys
 *
 * Hinweis: OIDC refresh läuft via Browser-redirect (silent refresh) oder
 * refresh-token-exchange. Die tatsächliche Logik bleibt in OidcAuthService;
 * OidcIdentitySource ist der Sphären-Wrapper mit BehaviorSubject.
 */
@Injectable({ providedIn: 'root' })
export class OidcIdentitySource implements IdentitySource, OnDestroy {
  readonly sphere = 'oidc' as const;
  private readonly _snapshot$ = new BehaviorSubject<IdentitySnapshot>({ status: 'absent' });
  readonly snapshot$: Observable<IdentitySnapshot> = this._snapshot$.asObservable();

  private readonly auth = inject(UserAuthService);
  private readonly oidc = inject(OidcAuthService);
  private readonly tokenSubscription: Subscription;
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    this.tokenSubscription = this.auth.oidcToken$.subscribe((token) => {
      if (!token) {
        if (this._snapshot$.value.status !== 'absent') {
          this._snapshot$.next(buildSnapshot({ status: 'absent' }));
        }
        return;
      }
      if (this._snapshot$.value.token === token) return;
      this._snapshot$.next(snapshotFromJwt(token, undefined, 'oidc'));
      this.scheduleRefresh();
    });
  }

  get current(): IdentitySnapshot {
    return this._snapshot$.value;
  }

  async restoreFromStorage(): Promise<void> {
    const at = localStorage.getItem(IDENTITY_STORAGE_LAYOUT.oidc.accessToken.key);
    if (!at) {
      this._snapshot$.next(buildSnapshot({ status: 'absent' }));
      return;
    }
    const rt = await this.auth.getOidcRefreshToken();
    const snap = snapshotFromJwt(at, rt ?? undefined, 'oidc');
    this._snapshot$.next(snap);
    this.scheduleRefresh();
  }

  /**
   * Called by OidcAuthService after a successful PKCE callback or refresh.
   */
  async onAuthenticated(accessToken: string, refreshToken?: string): Promise<void> {
    this.auth.setOidcAccessToken(accessToken);
    await this.auth.setOidcRefreshToken(refreshToken ?? null);
    const snap = snapshotFromJwt(accessToken, refreshToken, 'oidc');
    this._snapshot$.next(snap);
    this.scheduleRefresh();
  }

  async refresh(): Promise<void> {
    try {
      const refreshed = await this.oidc.refreshFromStorage();
      if (!refreshed) {
        this._snapshot$.next(
          buildSnapshot({ status: 'expired', error: 'oidc refresh failed' }),
        );
        return;
      }
      const accessToken = this.auth.oidcAccessTokenValue;
      if (!accessToken) {
        this._snapshot$.next(buildSnapshot({ status: 'expired', error: 'oidc token missing' }));
        return;
      }
      const refreshToken = await this.auth.getOidcRefreshToken();
      this._snapshot$.next(snapshotFromJwt(accessToken, refreshToken ?? undefined, 'oidc'));
      this.scheduleRefresh();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'oidc refresh failed';
      this._snapshot$.next(buildSnapshot({ status: 'expired', error: msg }));
    }
  }

  logout(): void {
    this.auth.setOidcAccessToken(null);
    void this.auth.setOidcRefreshToken(null);
    this._snapshot$.next(buildSnapshot({ status: 'absent' }));
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  private scheduleRefresh(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    const snap = this._snapshot$.value;
    if (snap.status !== 'ready' || !snap.refreshAfter) return;
    const now = Date.now() / 1000;
    const delayMs = Math.max(0, (snap.refreshAfter - now) * 1000);
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      const s = this._snapshot$.value;
      if (needsRefresh(s)) {
        void this.refresh();
      }
    }, delayMs);
  }

  ngOnDestroy(): void {
    this.tokenSubscription.unsubscribe();
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
  }
}
