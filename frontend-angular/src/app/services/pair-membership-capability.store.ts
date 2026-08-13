import { Injectable } from '@angular/core';

export type PairMembershipAttemptKind = 'create' | 'join';

export interface PairMembershipAuthorityScope {
  readonly baseUrl: string;
  readonly oidcIssuer: string;
  readonly oidcSubject: string;
}

export interface PairMembershipAttemptScope extends PairMembershipAuthorityScope {
  readonly kind: PairMembershipAttemptKind;
}

export interface PairMembershipPendingAttempt {
  readonly scope: Readonly<PairMembershipAttemptScope>;
  readonly capability: string;
  readonly body: Readonly<Record<string, unknown>>;
  readonly intent: Readonly<Record<string, unknown>>;
  readonly createdAtMs: number;
}

export interface PairMembershipCapabilityBinding extends PairMembershipAuthorityScope {
  readonly sessionId: string;
  readonly localPeerId: string;
  readonly capability: string;
}

const PENDING_PREFIX = 'ananta.pair-membership-capability.pending.v2.';
const BOUND_PREFIX = 'ananta.pair-membership-capability.bound.v2.';
const IDENTIFIER_RE = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;
const CAPABILITY_RE = /^[A-Za-z0-9_-]{43}$/;
export const MAX_PUBLIC_PAIR_CATALOG_MEMBERSHIPS = 32;
const PENDING_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const CLOCK_SKEW_MS = 5 * 60 * 1000;

/**
 * Persists v2 membership proofs only for the lifetime of the browser tab.
 *
 * Pending proof is durable before a mutating request leaves the browser, so a
 * lost create/join response can be retried with the same idempotency secret.
 * Public catalog responses may discover metadata only for an exact bounded
 * set of already-bound proofs; they can never create or broaden a proof.
 */
@Injectable({ providedIn: 'root' })
export class PairMembershipCapabilityStore {
  begin(
    scope: PairMembershipAttemptScope,
    requestBody: Record<string, unknown>,
    logicalIntent: Record<string, unknown> = requestBody,
  ): Readonly<PairMembershipPendingAttempt> {
    const normalizedScope = normalizeScope(scope);
    const desiredBody = canonicalRequestBody(requestBody);
    const desiredIntent = canonicalRequestBody(logicalIntent);
    const key = pendingKey(normalizedScope.kind);
    let existing = readPending(key, normalizedScope.kind);
    if (existing && pendingExpired(existing)) {
      removeStorageItem(key);
      existing = null;
    }
    if (existing) {
      if (
        !sameScope(existing.scope, normalizedScope)
        || JSON.stringify(existing.intent) !== JSON.stringify(desiredIntent)
      ) throw new Error('public_pair_pending_attempt_conflict');
      return existing;
    }
    const pending = Object.freeze({
      scope: normalizedScope,
      capability: generateCapability(),
      body: desiredBody,
      intent: desiredIntent,
      createdAtMs: Date.now(),
    });
    writeAndVerify(key, JSON.stringify(pending));
    const persisted = readPending(key, normalizedScope.kind);
    if (!persisted || persisted.capability !== pending.capability) {
      throw new Error('public_membership_capability_storage_unavailable');
    }
    return persisted;
  }

  promote(
    scope: PairMembershipAttemptScope,
    sessionId: string,
    localPeerId: string,
    expectedCapability: string,
  ): Readonly<PairMembershipCapabilityBinding> {
    const normalizedScope = normalizeScope(scope);
    const pending = readPending(pendingKey(normalizedScope.kind), normalizedScope.kind);
    const validatedExpected = validPairMembershipCapability(expectedCapability);
    if (!pending) {
      const existing = readBound(
        boundKey(sessionId, localPeerId), sessionId, localPeerId, normalizedScope,
      );
      if (existing?.capability === validatedExpected) return existing;
      throw new Error('public_membership_capability_pending_missing');
    }
    if (pendingExpired(pending)) throw new Error('public_membership_capability_pending_expired');
    if (!sameScope(pending.scope, normalizedScope)) {
      throw new Error('public_pair_pending_attempt_conflict');
    }
    if (pending.capability !== validatedExpected) throw new Error('pair_membership_capability_conflict');
    const capability = validatedExpected;
    const normalized = normalizeBinding({
      sessionId,
      localPeerId,
      capability,
      baseUrl: normalizedScope.baseUrl,
      oidcIssuer: normalizedScope.oidcIssuer,
      oidcSubject: normalizedScope.oidcSubject,
    });
    const key = boundKey(sessionId, localPeerId);
    const existing = readBound(key, sessionId, localPeerId, normalizedScope);
    if (existing && existing.capability !== capability) {
      throw new Error('pair_membership_capability_conflict');
    }
    writeAndVerify(key, JSON.stringify(normalized));
    const persisted = readBound(key, sessionId, localPeerId, normalizedScope);
    if (!persisted || persisted.capability !== capability) {
      throw new Error('public_membership_capability_storage_unavailable');
    }
    removeStorageItem(pendingKey(normalizedScope.kind));
    return persisted;
  }

  require(
    sessionId: string,
    localPeerId: string,
    scope: PairMembershipAuthorityScope,
  ): string {
    const binding = this.find(sessionId, localPeerId, scope);
    if (!binding) throw new Error('public_membership_capability_missing');
    return binding;
  }

  find(
    sessionId: string,
    localPeerId: string,
    scope: PairMembershipAuthorityScope,
  ): string | null {
    return readBound(
      boundKey(sessionId, localPeerId), sessionId, localPeerId, normalizeAuthorityScope(scope),
    )?.capability ?? null;
  }

  /** Returns every bound proof for one exact OIDC authority in stable order. */
  listBound(
    scope: PairMembershipAuthorityScope,
  ): readonly Readonly<PairMembershipCapabilityBinding>[] {
    const expectedScope = normalizeAuthorityScope(scope);
    const matches: Readonly<PairMembershipCapabilityBinding>[] = [];
    for (const key of boundStorageKeys()) {
      const raw = readStorageItem(key);
      if (!raw) continue;
      let binding: Readonly<PairMembershipCapabilityBinding>;
      try {
        binding = normalizeBinding(JSON.parse(raw) as PairMembershipCapabilityBinding);
      } catch {
        throw new Error('public_membership_capability_binding_invalid');
      }
      if (sameAuthorityScope(binding, expectedScope)) matches.push(binding);
    }
    matches.sort((left, right) => (
      left.sessionId.localeCompare(right.sessionId)
      || left.localPeerId.localeCompare(right.localPeerId)
    ));
    return Object.freeze(matches);
  }

  clearPending(kind: PairMembershipAttemptKind): void {
    removeStorageItem(pendingKey(kind));
  }

  forget(
    sessionId: string,
    localPeerId: string,
    scope: PairMembershipAuthorityScope,
  ): void {
    const key = boundKey(sessionId, localPeerId);
    readBound(key, sessionId, localPeerId, normalizeAuthorityScope(scope));
    removeStorageItem(key);
  }
}

export function validPairMembershipCapability(value: unknown): string {
  if (typeof value !== 'string' || !CAPABILITY_RE.test(value)) {
    throw new Error('public_membership_capability_invalid');
  }
  return value;
}

function pendingKey(kind: PairMembershipAttemptKind): string {
  return `${PENDING_PREFIX}${kind}`;
}

function boundKey(sessionId: string, localPeerId: string): string {
  return `${BOUND_PREFIX}${encodeURIComponent(sessionId)}.${encodeURIComponent(localPeerId)}`;
}

function boundStorageKeys(): readonly string[] {
  try {
    const keys: string[] = [];
    for (let index = 0; index < sessionStorage.length; index += 1) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(BOUND_PREFIX)) keys.push(key);
    }
    return keys;
  } catch {
    throw new Error('public_membership_capability_storage_unavailable');
  }
}

function readPending(
  key: string,
  expectedKind: PairMembershipAttemptKind,
): Readonly<PairMembershipPendingAttempt> | null {
  const raw = readStorageItem(key);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as {
      scope?: unknown;
      capability?: unknown;
      body?: unknown;
      intent?: unknown;
      createdAtMs?: unknown;
    };
    const scope = normalizeScope(value.scope);
    if (scope.kind !== expectedKind) throw new Error('kind');
    return Object.freeze({
      scope,
      capability: validPairMembershipCapability(value.capability),
      body: canonicalRequestBody(value.body),
      intent: canonicalRequestBody(value.intent),
      createdAtMs: validCreatedAt(value.createdAtMs),
    });
  } catch {
    throw new Error('public_membership_capability_pending_invalid');
  }
}

function readBound(
  key: string,
  expectedSessionId: string,
  expectedLocalPeerId: string,
  expectedScope: PairMembershipAuthorityScope,
): Readonly<PairMembershipCapabilityBinding> | null {
  const raw = readStorageItem(key);
  if (!raw) return null;
  try {
    const normalized = normalizeBinding(JSON.parse(raw) as PairMembershipCapabilityBinding);
    if (
      normalized.sessionId !== expectedSessionId
      || normalized.localPeerId !== expectedLocalPeerId
      || !sameAuthorityScope(normalized, expectedScope)
    ) throw new Error('scope');
    return normalized;
  } catch {
    throw new Error('public_membership_capability_binding_invalid');
  }
}

function normalizeBinding(
  value: PairMembershipCapabilityBinding,
): Readonly<PairMembershipCapabilityBinding> {
  if (
    !value
    || !IDENTIFIER_RE.test(String(value.sessionId || ''))
    || !IDENTIFIER_RE.test(String(value.localPeerId || ''))
  ) throw new Error('pair_membership_capability_binding_invalid');
  return Object.freeze({
    sessionId: value.sessionId,
    localPeerId: value.localPeerId,
    capability: validPairMembershipCapability(value.capability),
    ...normalizeAuthorityScope(value),
  });
}

function generateCapability(): string {
  const random = new Uint8Array(32);
  crypto.getRandomValues(random);
  const binary = Array.from(random, byte => String.fromCharCode(byte)).join('');
  return btoa(binary).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}

function normalizeScope(value: unknown): Readonly<PairMembershipAttemptScope> {
  const candidate = value as Partial<PairMembershipAttemptScope> | null;
  if (
    !candidate
    || (candidate.kind !== 'create' && candidate.kind !== 'join')
    || !boundedString(candidate.baseUrl, 2048)
    || !boundedString(candidate.oidcIssuer, 2048)
    || !boundedString(candidate.oidcSubject, 512)
  ) throw new Error('public_pair_pending_scope_invalid');
  return Object.freeze({
    kind: candidate.kind,
    ...normalizeAuthorityScope(candidate),
  });
}

function normalizeAuthorityScope(value: unknown): Readonly<PairMembershipAuthorityScope> {
  const candidate = value as Partial<PairMembershipAuthorityScope> | null;
  if (
    !candidate
    || !boundedString(candidate.baseUrl, 2048)
    || !boundedString(candidate.oidcIssuer, 2048)
    || !boundedString(candidate.oidcSubject, 512)
  ) throw new Error('public_membership_capability_scope_invalid');
  return Object.freeze({
    baseUrl: candidate.baseUrl,
    oidcIssuer: candidate.oidcIssuer,
    oidcSubject: candidate.oidcSubject,
  });
}

function sameScope(
  left: Readonly<PairMembershipAttemptScope>,
  right: Readonly<PairMembershipAttemptScope>,
): boolean {
  return left.kind === right.kind
    && sameAuthorityScope(left, right);
}

function sameAuthorityScope(
  left: PairMembershipAuthorityScope,
  right: PairMembershipAuthorityScope,
): boolean {
  return left.baseUrl === right.baseUrl
    && left.oidcIssuer === right.oidcIssuer
    && left.oidcSubject === right.oidcSubject;
}

function boundedString(value: unknown, maxLength: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength;
}

function validCreatedAt(value: unknown): number {
  if (
    typeof value !== 'number'
    || !Number.isSafeInteger(value)
    || value <= 0
    || value > Date.now() + CLOCK_SKEW_MS
  ) throw new Error('public_pair_pending_created_at_invalid');
  return value;
}

function pendingExpired(value: PairMembershipPendingAttempt): boolean {
  return Date.now() - value.createdAtMs >= PENDING_MAX_AGE_MS;
}

function canonicalRequestBody(value: unknown): Readonly<Record<string, unknown>> {
  const canonical = canonicalJson(value);
  if (!canonical || typeof canonical !== 'object' || Array.isArray(canonical)) {
    throw new Error('public_pair_request_invalid');
  }
  return deepFreeze(canonical as Record<string, unknown>);
}

function canonicalJson(value: unknown): unknown {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('public_pair_request_invalid');
    return value;
  }
  if (Array.isArray(value)) return value.map(item => canonicalJson(item));
  if (!value || typeof value !== 'object') throw new Error('public_pair_request_invalid');
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value as Record<string, unknown>).sort()) {
    const item = (value as Record<string, unknown>)[key];
    if (item === undefined) continue;
    result[key] = canonicalJson(item);
  }
  return result;
}

function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value as Record<string, unknown>)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

function writeAndVerify(key: string, raw: string): void {
  try {
    sessionStorage.setItem(key, raw);
    if (sessionStorage.getItem(key) !== raw) throw new Error('readback');
  } catch {
    try { sessionStorage.removeItem(key); } catch { /* storage is unavailable */ }
    throw new Error('public_membership_capability_storage_unavailable');
  }
}

function readStorageItem(key: string): string | null {
  try { return sessionStorage.getItem(key); } catch {
    throw new Error('public_membership_capability_storage_unavailable');
  }
}

function removeStorageItem(key: string): void {
  try { sessionStorage.removeItem(key); } catch {
    throw new Error('public_membership_capability_storage_unavailable');
  }
}
