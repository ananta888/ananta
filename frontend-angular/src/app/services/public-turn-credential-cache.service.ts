import { Injectable } from '@angular/core';

import { rateLimitRetryAfterMs } from './http-rate-limit';

export interface PublicTurnCredentials {
  readonly username: string;
  readonly password: string;
  readonly ttl: number;
  readonly uris: readonly string[];
}

/** Secret-free identity of the immutable authority allowed to issue TURN credentials. */
export interface PublicTurnCredentialCacheKey {
  readonly authorityBaseUrl: string;
  readonly sessionId: string;
  readonly localPeerId: string;
  readonly identityBindingVersion: 1 | 2;
  readonly identityAuthority: string;
}

interface CachedCredentials {
  readonly sessionId: string;
  readonly credentials: PublicTurnCredentials;
  readonly usableUntilMs: number;
}

interface PendingCredentials {
  readonly sessionId: string;
  readonly revision: number;
  readonly promise: Promise<PublicTurnCredentials | null>;
}

interface Cooldown {
  readonly sessionId: string;
  readonly untilMs: number;
}

const MAX_EXPIRY_SKEW_MS = 30_000;
const MIN_EXPIRY_SKEW_MS = 1_000;
const EXPIRY_SKEW_RATIO = 0.1;
const LEGACY_TURN_RATE_LIMIT_WINDOW_MS = 60_000;
export const MAX_PUBLIC_TURN_CREDENTIAL_TTL_SECONDS = 24 * 60 * 60;

/**
 * Process-local TURN secret cache.
 *
 * This service deliberately knows nothing about HTTP or Pair bindings. Its
 * caller supplies an already-authorized, secret-free cache key and a loader,
 * keeping authority validation in the control plane (DIP). Credentials never
 * reach browser persistence and rejected loaders are never cached.
 */
@Injectable({ providedIn: 'root' })
export class PublicTurnCredentialCacheService {
  private readonly cached = new Map<string, CachedCredentials>();
  private readonly pending = new Map<string, PendingCredentials>();
  private readonly cooldowns = new Map<string, Cooldown>();
  private readonly activeScopeBySession = new Map<string, { scope: string; revision: number }>();
  private scopeRevision = 0;

  get(
    key: PublicTurnCredentialCacheKey,
    loader: () => Promise<PublicTurnCredentials | null>,
  ): Promise<PublicTurnCredentials | null> {
    const scope = cacheScope(key);
    const nowMs = Date.now();
    this.prune(nowMs);
    const revision = this.selectScope(key.sessionId, scope);

    const cached = this.cached.get(scope);
    if (cached && cached.usableUntilMs > nowMs) {
      return Promise.resolve(cached.credentials);
    }

    const cooldown = this.cooldowns.get(scope);
    if (cooldown && cooldown.untilMs > nowMs) {
      return Promise.reject(new PublicTurnCredentialCooldownError(cooldown.untilMs - nowMs));
    }

    const existing = this.pending.get(scope);
    if (existing?.revision === revision) return existing.promise;

    let request!: PendingCredentials;
    const promise = this.load(scope, key.sessionId, revision, loader).finally(() => {
      if (this.pending.get(scope) === request) this.pending.delete(scope);
    });
    request = { sessionId: key.sessionId, revision, promise };
    this.pending.set(scope, request);
    return promise;
  }

  invalidateSession(sessionId: string): void {
    for (const [scope, entry] of this.cached) {
      if (entry.sessionId === sessionId) this.cached.delete(scope);
    }
    for (const [scope, entry] of this.cooldowns) {
      if (entry.sessionId === sessionId) this.cooldowns.delete(scope);
    }
    for (const [scope, entry] of this.pending) {
      if (entry.sessionId === sessionId) this.pending.delete(scope);
    }
    // Pending HTTP requests cannot safely be cancelled at this abstraction
    // boundary. Removing their scope prevents the eventual result from being
    // accepted or cached by load(). The WebRTC generation fence covers its
    // awaiting caller as well.
    this.activeScopeBySession.delete(sessionId);
    this.nextRevision();
  }

  private async load(
    scope: string,
    sessionId: string,
    revision: number,
    loader: () => Promise<PublicTurnCredentials | null>,
  ): Promise<PublicTurnCredentials | null> {
    try {
      const loaded = await loader();
      if (!this.isActiveScope(sessionId, scope, revision)) {
        throw new Error('public_turn_binding_superseded');
      }
      if (!loaded) return null;
      const credentials = immutableCredentials(loaded);
      const ttlMs = credentials.ttl * 1_000;
      const skewMs = Math.min(
        MAX_EXPIRY_SKEW_MS,
        Math.max(MIN_EXPIRY_SKEW_MS, Math.floor(ttlMs * EXPIRY_SKEW_RATIO)),
      );
      const usableUntilMs = Date.now() + Math.max(0, ttlMs - skewMs);
      if (usableUntilMs > Date.now()) {
        this.cached.set(scope, { sessionId, credentials, usableUntilMs });
      }
      this.cooldowns.delete(scope);
      return credentials;
    } catch (error) {
      // Older deployed rendezvous revisions did not emit Retry-After. Their
      // TURN bucket is one minute, so the generic ten-second fallback would
      // still churn the same bucket before it can recover.
      const retryAfterMs = rateLimitRetryAfterMs(error, LEGACY_TURN_RATE_LIMIT_WINDOW_MS);
      if (
        retryAfterMs !== null
        && this.isActiveScope(sessionId, scope, revision)
      ) {
        this.cooldowns.set(scope, {
          sessionId,
          untilMs: Date.now() + retryAfterMs,
        });
      }
      throw error;
    }
  }

  private selectScope(sessionId: string, scope: string): number {
    const activeScope = this.activeScopeBySession.get(sessionId);
    if (activeScope?.scope === scope) return activeScope.revision;
    if (activeScope) {
      this.cached.delete(activeScope.scope);
      this.cooldowns.delete(activeScope.scope);
      this.pending.delete(activeScope.scope);
    }
    const revision = this.nextRevision();
    this.activeScopeBySession.set(sessionId, { scope, revision });
    return revision;
  }

  private isActiveScope(sessionId: string, scope: string, revision: number): boolean {
    const active = this.activeScopeBySession.get(sessionId);
    return active?.scope === scope && active.revision === revision;
  }

  private nextRevision(): number {
    this.scopeRevision += 1;
    if (!Number.isSafeInteger(this.scopeRevision)) this.scopeRevision = 1;
    return this.scopeRevision;
  }

  private prune(nowMs: number): void {
    for (const [scope, entry] of this.cached) {
      if (entry.usableUntilMs <= nowMs) this.cached.delete(scope);
    }
    for (const [scope, entry] of this.cooldowns) {
      if (entry.untilMs <= nowMs) this.cooldowns.delete(scope);
    }
  }
}

/** A local cooldown result; it intentionally carries no server response body. */
export class PublicTurnCredentialCooldownError extends Error {
  readonly status = 429;
  readonly retryAfterMs: number;
  readonly headers: { get(name: string): string | null };

  constructor(retryAfterMs: number) {
    super('public_turn_credentials_rate_limited');
    this.name = 'PublicTurnCredentialCooldownError';
    this.retryAfterMs = Math.max(1, Math.ceil(retryAfterMs));
    this.headers = Object.freeze({
      get: (name: string) => name.toLowerCase() === 'retry-after'
        ? String(Math.ceil(this.retryAfterMs / 1_000))
        : null,
    });
  }
}

function cacheScope(key: PublicTurnCredentialCacheKey): string {
  for (const value of [
    key.authorityBaseUrl,
    key.sessionId,
    key.localPeerId,
    key.identityAuthority,
  ]) {
    if (!value || typeof value !== 'string') throw new Error('public_turn_cache_key_invalid');
  }
  if (key.identityBindingVersion !== 1 && key.identityBindingVersion !== 2) {
    throw new Error('public_turn_cache_key_invalid');
  }
  return JSON.stringify([
    key.authorityBaseUrl,
    key.sessionId,
    key.localPeerId,
    key.identityBindingVersion,
    key.identityAuthority,
  ]);
}

function immutableCredentials(value: PublicTurnCredentials): PublicTurnCredentials {
  if (
    typeof value?.username !== 'string'
    || !value.username
    || typeof value.password !== 'string'
    || !value.password
    || !Number.isSafeInteger(value.ttl)
    || value.ttl <= 0
    || value.ttl > MAX_PUBLIC_TURN_CREDENTIAL_TTL_SECONDS
    || !Array.isArray(value.uris)
    || value.uris.length === 0
    || value.uris.some(uri => typeof uri !== 'string' || !/^turns?:/i.test(uri))
  ) throw new Error('public_turn_credentials_response_invalid');
  return Object.freeze({
    username: value.username,
    password: value.password,
    ttl: value.ttl,
    uris: Object.freeze([...value.uris]),
  });
}
