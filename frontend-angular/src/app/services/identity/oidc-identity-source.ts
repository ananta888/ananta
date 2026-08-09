import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Observable, Subscription } from 'rxjs';
import type { IdentitySnapshot, IdentitySource } from './identity.types';
import {
  buildSnapshot,
  needsRefresh,
} from './identity-snapshot';
import { inspectJwtAccessToken } from './jwt-access-token';
import { IDENTITY_STORAGE_LAYOUT } from './identity-storage-layout';
import { UserAuthService } from '../user-auth.service';
import { OidcAuthService } from '../oidc-auth.service';

const OIDC_REFRESH_RETRY_MILLISECONDS = 10_000;

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
      this._snapshot$.next(snapshotFromOidcAccessToken(token));
      this.scheduleRefresh();
    });
  }

  get current(): IdentitySnapshot {
    return this._snapshot$.value;
  }

  async restoreFromStorage(): Promise<void> {
    const at = localStorage.getItem(IDENTITY_STORAGE_LAYOUT.oidc.accessToken.key);
    const generation = this.auth.oidcSessionGenerationValue;
    if (!at) {
      const rt = await this.auth.getOidcRefreshToken();
      if (await this.adoptRestoreReplacement(generation, at)) return;
      if (!rt) {
        this._snapshot$.next(buildSnapshot({ status: 'absent' }));
        return;
      }
      this._snapshot$.next(buildSnapshot({
        status: 'authenticating', refreshToken: rt, issuer: 'oidc',
      }));
      await this.refresh();
      return;
    }
    if (this.auth.oidcAccessTokenValue !== at) {
      this.auth.setOidcAccessToken(at);
    }
    const synchronizedGeneration = this.auth.oidcSessionGenerationValue;
    const rt = await this.auth.getOidcRefreshToken();
    if (await this.adoptRestoreReplacement(synchronizedGeneration, at)) return;
    const snap = snapshotFromOidcAccessToken(at, rt ?? undefined);
    this._snapshot$.next(snap);
    if (snap.status !== 'ready') {
      await this.refresh();
      return;
    }
    this.scheduleRefresh();
  }

  /**
   * Called by OidcAuthService after a successful PKCE callback or refresh.
   */
  async onAuthenticated(accessToken: string, refreshToken?: string): Promise<void> {
    const commit = await this.oidc.commitAuthenticatedSession(
      accessToken,
      refreshToken ?? null,
    );
    if (!commit.committed) {
      await this.adoptCurrentSession();
      return;
    }
    const snap = snapshotFromOidcAccessToken(
      accessToken,
      commit.refreshTokenPersisted ? refreshToken : undefined,
    );
    this._snapshot$.next(snap);
    this.scheduleRefresh();
  }

  async refresh(): Promise<void> {
    const generation = this.auth.oidcSessionGenerationValue;
    const accessTokenAtStart = this.auth.oidcAccessTokenValue;
    try {
      const refreshed = await this.oidc.refreshFromStorage();
      if (!refreshed) {
        if (await this.adoptCurrentSession()) {
          if (generation !== this.auth.oidcSessionGenerationValue) {
            this.scheduleRefresh();
          } else if (this._snapshot$.value.refreshToken) {
            this.scheduleRefreshRetry();
          } else {
            this.scheduleAccessTokenExpiry();
          }
          return;
        }
        this.markAccessTokenExpired(
          'oidc refresh failed',
          generation,
          accessTokenAtStart,
        );
        return;
      }
      if (await this.adoptCurrentSession()) {
        this.scheduleRefresh();
      } else {
        const invalidGeneration = this.auth.oidcSessionGenerationValue;
        const invalidAccessToken = this.auth.oidcAccessTokenValue;
        this.markAccessTokenExpired(
          'oidc access token invalid after refresh',
          invalidGeneration,
          invalidAccessToken,
        );
      }
    } catch (err: unknown) {
      if (await this.adoptCurrentSession()) {
        if (generation !== this.auth.oidcSessionGenerationValue) {
          this.scheduleRefresh();
        } else if (this._snapshot$.value.refreshToken) {
          this.scheduleRefreshRetry();
        } else {
          this.scheduleAccessTokenExpiry();
        }
        return;
      }
      const msg = err instanceof Error ? err.message : 'oidc refresh failed';
      this.markAccessTokenExpired(msg, generation, accessTokenAtStart);
    }
  }

  logout(): void {
    this.oidc.logoutLocal();
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

  private scheduleRefreshRetry(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    const expiresAt = this._snapshot$.value.expiresAt;
    const untilExpiry = expiresAt === undefined
      ? OIDC_REFRESH_RETRY_MILLISECONDS
      : Math.max(0, expiresAt * 1000 - Date.now());
    const delay = Math.min(OIDC_REFRESH_RETRY_MILLISECONDS, untilExpiry);
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      void this.refresh();
    }, delay);
  }

  private scheduleAccessTokenExpiry(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    const snapshot = this._snapshot$.value;
    const generation = this.auth.oidcSessionGenerationValue;
    const accessToken = this.auth.oidcAccessTokenValue;
    if (!snapshot.expiresAt || !accessToken) return;
    const delayMs = Math.max(0, (snapshot.expiresAt - Date.now() / 1000) * 1000);
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      this.markAccessTokenExpired(
        'oidc access token expired; secure refresh storage unavailable',
        generation,
        accessToken,
      );
    }, delayMs);
  }

  private async adoptCurrentSession(): Promise<boolean> {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const generation = this.auth.oidcSessionGenerationValue;
      const accessToken = this.auth.oidcAccessTokenValue;
      if (!accessToken) return false;
      const refreshToken = await this.auth.getOidcRefreshToken();
      if (
        generation !== this.auth.oidcSessionGenerationValue
        || accessToken !== this.auth.oidcAccessTokenValue
      ) continue;
      const snapshot = snapshotFromOidcAccessToken(accessToken, refreshToken ?? undefined);
      if (snapshot.status !== 'ready') return false;
      this._snapshot$.next(snapshot);
      return true;
    }
    return false;
  }

  private async adoptRestoreReplacement(
    expectedGeneration: number,
    expectedAccessToken: string | null,
  ): Promise<boolean> {
    if (
      expectedGeneration === this.auth.oidcSessionGenerationValue
      && expectedAccessToken === localStorage.getItem(
        IDENTITY_STORAGE_LAYOUT.oidc.accessToken.key,
      )
    ) return false;
    if (await this.adoptCurrentSession()) this.scheduleRefresh();
    return true;
  }

  private markAccessTokenExpired(
    error: string,
    expectedGeneration: number,
    expectedAccessToken: string | null,
  ): void {
    // Never let a stale access token advertise an authenticated browser
    // session. The encrypted refresh token may remain for an explicit retry,
    // but it cannot authorize a request by itself.
    const cleared = this.auth.setOidcAccessTokenIfGeneration(
      null,
      expectedGeneration,
      expectedAccessToken,
    );
    if (!cleared) {
      void this.adoptCurrentSession();
      return;
    }
    this._snapshot$.next(buildSnapshot({ status: 'expired', error }));
  }

  ngOnDestroy(): void {
    this.tokenSubscription.unsubscribe();
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
  }
}

function snapshotFromOidcAccessToken(token: string, refreshToken?: string): IdentitySnapshot {
  const inspection = inspectJwtAccessToken(token);
  if (inspection.ok === false) {
    return buildSnapshot({
      status: 'expired',
      token,
      refreshToken,
      issuer: 'oidc',
      error: `oidc_access_token_${inspection.reason}`,
    });
  }
  return buildSnapshot({
    status: 'ready',
    token,
    refreshToken,
    subject: inspection.subject,
    issuer: 'oidc',
    expiresAt: inspection.expiresAt,
  });
}
