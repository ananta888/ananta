import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  PublicTurnCredentialCacheKey,
  PublicTurnCredentialCacheService,
  PublicTurnCredentialCooldownError,
  PublicTurnCredentials,
} from './public-turn-credential-cache.service';

const KEY: PublicTurnCredentialCacheKey = Object.freeze({
  authorityBaseUrl: 'https://webrtc.ananta.de',
  sessionId: 'session-a',
  localPeerId: 'peer:alice',
  identityBindingVersion: 2,
  identityAuthority: 'https://issuer.example\0subject-alice',
});

const CREDENTIALS: PublicTurnCredentials = Object.freeze({
  username: 'expiry:alice',
  password: 'credential',
  ttl: 600,
  uris: Object.freeze(['turn:webrtc.ananta.de:3478']),
});

describe('PublicTurnCredentialCacheService', () => {
  let cache: PublicTurnCredentialCacheService;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-11T08:00:00Z'));
    cache = new PublicTurnCredentialCacheService();
  });

  afterEach(() => vi.useRealTimers());

  it('reuses credentials only within their TTL safety skew', async () => {
    const loader = vi.fn(async () => CREDENTIALS);

    const first = await cache.get(KEY, loader);
    vi.advanceTimersByTime(569_999);
    const cached = await cache.get(KEY, loader);
    vi.advanceTimersByTime(1);
    const refreshed = await cache.get(KEY, loader);

    expect(first).toEqual(CREDENTIALS);
    expect(Object.isFrozen(first)).toBe(true);
    expect(cached).toBe(first);
    expect(refreshed).not.toBe(first);
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('coalesces one exact in-flight binding request', async () => {
    const response = deferred<PublicTurnCredentials>();
    const loader = vi.fn(() => response.promise);

    const first = cache.get(KEY, loader);
    const second = cache.get({ ...KEY }, loader);

    expect(first).toBe(second);
    expect(loader).toHaveBeenCalledOnce();
    response.resolve(CREDENTIALS);
    await expect(first).resolves.toEqual(CREDENTIALS);
    await expect(second).resolves.toEqual(CREDENTIALS);
  });

  it('does not share credentials across a local-peer binding change', async () => {
    const aliceLoader = vi.fn(async () => CREDENTIALS);
    const bobCredentials = { ...CREDENTIALS, username: 'expiry:bob' };
    const bobLoader = vi.fn(async () => bobCredentials);

    await cache.get(KEY, aliceLoader);
    await cache.get({ ...KEY, localPeerId: 'peer:bob' }, bobLoader);
    await cache.get(KEY, aliceLoader);

    expect(aliceLoader).toHaveBeenCalledTimes(2);
    expect(bobLoader).toHaveBeenCalledOnce();
  });

  it('honors Retry-After cooldown without caching the rejected loader', async () => {
    const rateLimit = {
      status: 429,
      headers: { get: (name: string) => name === 'Retry-After' ? '5' : null },
    };
    const loader = vi.fn()
      .mockRejectedValueOnce(rateLimit)
      .mockResolvedValueOnce(CREDENTIALS);

    await expect(cache.get(KEY, loader)).rejects.toBe(rateLimit);
    await expect(cache.get(KEY, loader)).rejects.toMatchObject({
      status: 429,
      retryAfterMs: 5_000,
    });
    expect(loader).toHaveBeenCalledOnce();

    vi.advanceTimersByTime(5_000);
    await expect(cache.get(KEY, loader)).resolves.toEqual(CREDENTIALS);
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('uses the legacy one-minute TURN window when Retry-After is absent', async () => {
    const rateLimit = { status: 429 };
    const loader = vi.fn()
      .mockRejectedValueOnce(rateLimit)
      .mockResolvedValueOnce(CREDENTIALS);

    await expect(cache.get(KEY, loader)).rejects.toBe(rateLimit);
    vi.advanceTimersByTime(59_999);
    await expect(cache.get(KEY, loader)).rejects.toMatchObject({
      status: 429,
      retryAfterMs: 1,
    });
    expect(loader).toHaveBeenCalledOnce();

    vi.advanceTimersByTime(1);
    await expect(cache.get(KEY, loader)).resolves.toEqual(CREDENTIALS);
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('never caches non-rate-limit loader failures', async () => {
    const loader = vi.fn()
      .mockRejectedValueOnce(new Error('temporary_network_failure'))
      .mockResolvedValueOnce(CREDENTIALS);

    await expect(cache.get(KEY, loader)).rejects.toThrow('temporary_network_failure');
    await expect(cache.get(KEY, loader)).resolves.toEqual(CREDENTIALS);

    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('rejects an unsafe TTL instead of turning overflow into an unbounded cache entry', async () => {
    const loader = vi.fn(async () => ({ ...CREDENTIALS, ttl: Number.MAX_VALUE }));

    await expect(cache.get(KEY, loader))
      .rejects.toThrow('public_turn_credentials_response_invalid');
    await expect(cache.get(KEY, loader))
      .rejects.toThrow('public_turn_credentials_response_invalid');

    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('invalidates cached and pending secrets for an ended session', async () => {
    const stale = deferred<PublicTurnCredentials>();
    const staleLoader = vi.fn(() => stale.promise);
    const oldRequest = cache.get(KEY, staleLoader);
    cache.invalidateSession(KEY.sessionId);

    const freshLoader = vi.fn(async () => ({ ...CREDENTIALS, username: 'expiry:fresh' }));
    const freshRequest = cache.get(KEY, freshLoader);
    stale.resolve(CREDENTIALS);

    await expect(oldRequest).rejects.toThrow('public_turn_binding_superseded');
    await expect(freshRequest).resolves.toMatchObject({ username: 'expiry:fresh' });
    expect(staleLoader).toHaveBeenCalledOnce();
    expect(freshLoader).toHaveBeenCalledOnce();
  });

  it('exposes a local cooldown without retaining the rejected server error', () => {
    const cooldown = new PublicTurnCredentialCooldownError(1_250);

    expect(cooldown.message).toBe('public_turn_credentials_rate_limited');
    expect(cooldown.headers.get('Retry-After')).toBe('2');
    expect(cooldown).not.toHaveProperty('error');
  });
});

function deferred<T>(): { readonly promise: Promise<T>; resolve(value: T): void } {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>(resolve => { resolvePromise = resolve; });
  return { promise, resolve: resolvePromise };
}
