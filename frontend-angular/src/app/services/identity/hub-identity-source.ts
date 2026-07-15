import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Observable, Subscription, firstValueFrom } from 'rxjs';
import type { IdentitySnapshot, IdentitySource } from './identity.types';
import {
  buildSnapshot,
  needsRefresh,
  snapshotFromJwt,
} from './identity-snapshot';
import {
  IDENTITY_STORAGE_LAYOUT,
  clearAllIdentityStorage,
} from './identity-storage-layout';
import { UserAuthService } from '../user-auth.service';
import { AgentDirectoryService } from '../agent-directory.service';
import {
  HubRefreshSupersededError,
  isDefinitiveHubRefreshFailure,
} from '../hub-refresh-error';

const TRANSIENT_REFRESH_RETRY_MILLISECONDS = 30_000;

/**
 * HubIdentitySource — IdentitySource für die Hub-Sphäre.
 *
 * Lifecycle:
 *   1. restoreFromStorage() — liest access-token aus localStorage, lädt user
 *   2. snapshot$ — BehaviorSubject mit aktuellem Stand
 *   3. startProactiveRefresh() — Timer der Token vor Ablauf erneuert
 *   4. onSnapshot(snap) — erlaubt dem UserAuthService den Snapshot zu setzen
 *      (z.B. nach login)
 */
@Injectable({ providedIn: 'root' })
export class HubIdentitySource implements IdentitySource, OnDestroy {
  readonly sphere = 'hub' as const;
  private readonly _snapshot$ = new BehaviorSubject<IdentitySnapshot>({ status: 'absent' });
  readonly snapshot$: Observable<IdentitySnapshot> = this._snapshot$.asObservable();

  private readonly auth = inject(UserAuthService);
  private readonly dir = inject(AgentDirectoryService);
  private readonly tokenSubscription: Subscription;
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  private refreshInFlight: Promise<void> | null = null;

  constructor() {
    this.tokenSubscription = this.auth.token$.subscribe((token) => {
      if (!token) {
        if (this._snapshot$.value.status !== 'absent') {
          this._snapshot$.next(buildSnapshot({ status: 'absent' }));
        }
        return;
      }
      if (this._snapshot$.value.token === token) return;
      this._snapshot$.next(snapshotFromJwt(token, undefined, 'hub'));
      this.scheduleRefresh();
    });
  }

  /** Synchronous getter for current value (used by templates & other services). */
  get current(): IdentitySnapshot {
    return this._snapshot$.value;
  }

  /**
   * Read access token + RT from storage, populate snapshot.
   * Called once at app startup (before bootstrap completes).
   */
  async restoreFromStorage(): Promise<void> {
    const at = localStorage.getItem(IDENTITY_STORAGE_LAYOUT.hub.accessToken.key);
    if (!at) {
      this._snapshot$.next(buildSnapshot({ status: 'absent' }));
      return;
    }
    const rt = await this.auth.getHubRefreshToken();
    const snap = snapshotFromJwt(at, rt ?? undefined, 'hub');
    this._snapshot$.next(snap);
    if (snap.status === 'expired' && rt) {
      await this.refresh();
      return;
    }
    this.scheduleRefresh();
  }

  /**
   * Called by UserAuthService after a successful login (or refresh).
   * Stores access token (plaintext) and refresh token (encrypted), emits snapshot.
   */
  async onAuthenticated(accessToken: string, refreshToken?: string): Promise<void> {
    await this.auth.setTokens(accessToken, refreshToken ?? null);
    const snap = snapshotFromJwt(accessToken, refreshToken, 'hub');
    this._snapshot$.next(snap);
    this.scheduleRefresh();
  }

  async refresh(): Promise<void> {
    if (this.refreshInFlight) return this.refreshInFlight;
    this.refreshInFlight = this.doRefresh().finally(() => {
      this.refreshInFlight = null;
    });
    return this.refreshInFlight;
  }

  private async doRefresh(): Promise<void> {
    const current = this._snapshot$.value;
    if ((current.status !== 'ready' && current.status !== 'expired') || !current.token) return;
    const hub = this.dir.list().find((a) => a.role === 'hub');
    if (!hub) {
      if (this.auth.token !== current.token) return;
      await this.auth.setTokens(null, null);
      this._snapshot$.next(
        buildSnapshot({ status: 'expired', error: 'no hub in directory' }),
      );
      return;
    }
    const rt = await this.auth.getHubRefreshToken();
    if (this.auth.token !== current.token) return;
    if (!rt) {
      await this.auth.setTokens(null, null);
      this._snapshot$.next(
        buildSnapshot({ status: 'expired', error: 'no refresh token' }),
      );
      return;
    }
    try {
      // UserAuthService owns the one shared refresh operation. Proactive timer
      // refresh and interceptor-driven 401 recovery therefore cannot rotate
      // the same refresh token twice.
      const response = await firstValueFrom(this.auth.refreshToken());
      if (this.auth.token !== response.access_token) return;
      this._snapshot$.next(snapshotFromJwt(response.access_token, response.refresh_token, 'hub'));
      this.scheduleRefresh();
    } catch (err: unknown) {
      if (err instanceof HubRefreshSupersededError) return;
      if (this.auth.token !== current.token) return;
      const msg = err instanceof Error ? err.message : 'refresh failed';
      if (isDefinitiveHubRefreshFailure(err)) {
        // Hub and OIDC are independent identity spheres. A definitively invalid
        // Hub refresh clears only Hub credentials.
        await this.auth.setTokens(null, null);
        this._snapshot$.next(buildSnapshot({ status: 'expired', error: msg }));
        return;
      }
      // Network/429/5xx failures leave the session and rotated refresh token
      // intact, then retry proactively. Voice uploads can remain in the spool.
      this._snapshot$.next({ ...current, error: msg });
      this.scheduleRefreshRetry();
    }
  }

  logout(): void {
    void this.auth.setTokens(null, null);
    this._snapshot$.next(buildSnapshot({ status: 'absent' }));
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  /** Force-clear all identity-related storage. Test helper. */
  clearStorage(): void {
    clearAllIdentityStorage();
    this._snapshot$.next(buildSnapshot({ status: 'absent' }));
  }

  private scheduleRefresh(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    const snap = this._snapshot$.value;
    if (snap.status !== 'ready' || !snap.refreshAfter) return;
    const now = Date.now() / 1000;
    const delayMs = Math.max(0, (snap.refreshAfter - now) * 1000);
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      // Double-check at fire time, in case state changed
      const s = this._snapshot$.value;
      if (needsRefresh(s)) {
        void this.refresh();
      }
    }, delayMs);
  }

  private scheduleRefreshRetry(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      void this.refresh();
    }, TRANSIENT_REFRESH_RETRY_MILLISECONDS);
  }

  ngOnDestroy(): void {
    this.tokenSubscription.unsubscribe();
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
  }
}
