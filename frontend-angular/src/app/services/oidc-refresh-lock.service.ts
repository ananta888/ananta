import { Injectable } from '@angular/core';

const OIDC_REFRESH_LOCK_NAME = 'ananta.oidc.refresh.v1';

/**
 * Serializes OIDC refresh-token exchange across same-origin browser windows.
 *
 * The storage compare-and-swap in UserAuthService remains the fail-closed
 * fallback for browsers without Web Locks. This adapter owns only browser
 * coordination; token protocol and persistence stay outside it (SRP/DIP).
 */
@Injectable({ providedIn: 'root' })
export class OidcRefreshLock {
  private localTail: Promise<void> = Promise.resolve();

  async run<T>(operation: () => Promise<T>): Promise<T> {
    const predecessor = this.localTail;
    let release!: () => void;
    this.localTail = new Promise<void>((resolve) => { release = resolve; });
    await predecessor;
    try {
      const lockManager = globalThis.navigator?.locks;
      if (!lockManager) return await operation();
      return await lockManager.request(
        OIDC_REFRESH_LOCK_NAME,
        { mode: 'exclusive' },
        operation,
      );
    } finally {
      release();
    }
  }
}
