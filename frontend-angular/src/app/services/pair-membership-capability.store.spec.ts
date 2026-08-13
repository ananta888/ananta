import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  MAX_PUBLIC_PAIR_CATALOG_MEMBERSHIPS,
  PairMembershipCapabilityStore,
} from './pair-membership-capability.store';

const sessionId = 'session-a';
const localPeerId = `peer:${'a'.repeat(64)}`;
const createScope = {
  kind: 'create' as const,
  baseUrl: 'https://webrtc.ananta.de',
  oidcIssuer: 'https://keycloak.ananta.de/realms/ananta',
  oidcSubject: 'account-a',
};
const joinScope = { ...createScope, kind: 'join' as const };

describe('PairMembershipCapabilityStore', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    TestBed.resetTestingModule();
  });

  it('persists pending proof before promotion and restores only the bound tuple', () => {
    const first = TestBed.inject(PairMembershipCapabilityStore);
    const pending = first.begin(createScope, { title: 'Pair', expires_at: 123 });
    expect(pending.capability).toMatch(/^[A-Za-z0-9_-]{43}$/);

    first.promote(createScope, sessionId, localPeerId, pending.capability);
    TestBed.resetTestingModule();
    const restored = TestBed.inject(PairMembershipCapabilityStore);

    expect(restored.require(sessionId, localPeerId, createScope)).toBe(pending.capability);
    expect(restored.begin(createScope, { title: 'Next' }).capability).not.toBe(pending.capability);
    expect(Object.keys(localStorage)).toEqual([]);
  });

  it('reuses pending proof across an ambiguous response loss', () => {
    const first = TestBed.inject(PairMembershipCapabilityStore)
      .begin(joinScope, { invite_code: 'FIRST', expires_at: 123 }, { invite_code: 'FIRST' });
    TestBed.resetTestingModule();
    const retry = TestBed.inject(PairMembershipCapabilityStore)
      .begin(joinScope, { invite_code: 'CHANGED', expires_at: 999 }, { invite_code: 'FIRST' });

    expect(retry.capability).toBe(first.capability);
    expect(retry.body).toEqual({ expires_at: 123, invite_code: 'FIRST' });
  });

  it('fails before a request when sessionStorage cannot persist and read back the proof', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('denied');
    });
    try {
      expect(() => TestBed.inject(PairMembershipCapabilityStore).begin(createScope, { title: 'Pair' }))
        .toThrow('public_membership_capability_storage_unavailable');
    } finally {
      setItem.mockRestore();
    }
  });

  it('fails closed for a missing or differently scoped capability', () => {
    const store = TestBed.inject(PairMembershipCapabilityStore);

    expect(() => store.promote(joinScope, sessionId, localPeerId, 'A'.repeat(43)))
      .toThrow('public_membership_capability_pending_missing');
    expect(() => store.require(sessionId, `peer:${'b'.repeat(64)}`, createScope))
      .toThrow('public_membership_capability_missing');
  });

  it('clears definitive attempts and forgotten bound secrets', () => {
    const store = TestBed.inject(PairMembershipCapabilityStore);
    const abandoned = store.begin(createScope, { title: 'Pair' });
    store.clearPending('create');
    expect(store.begin(createScope, { title: 'Pair' }).capability).not.toBe(abandoned.capability);
    const current = store.begin(createScope, { title: 'Pair' });
    store.promote(createScope, sessionId, localPeerId, current.capability);
    store.forget(sessionId, localPeerId, createScope);

    expect(() => store.require(sessionId, localPeerId, createScope))
      .toThrow('public_membership_capability_missing');
  });

  it('rejects a changed intent or authenticated principal before proof reuse', () => {
    const store = TestBed.inject(PairMembershipCapabilityStore);
    store.begin(createScope, { title: 'Pair', expires_at: 123 }, { title: 'Pair', duration: 60 });

    expect(() => store.begin(
      createScope,
      { title: 'Changed', expires_at: 124 },
      { title: 'Changed', duration: 60 },
    )).toThrow('public_pair_pending_attempt_conflict');
    expect(() => store.begin(
      { ...createScope, oidcSubject: 'account-b' },
      { title: 'Pair', expires_at: 123 },
      { title: 'Pair', duration: 60 },
    )).toThrow('public_pair_pending_attempt_conflict');
  });

  it('replaces pending proof after the 24-hour server tombstone window', () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-08-09T08:00:00Z'));
      const store = TestBed.inject(PairMembershipCapabilityStore);
      const stale = store.begin(createScope, { title: 'Old' });
      vi.advanceTimersByTime(24 * 60 * 60 * 1000);

      const fresh = store.begin(createScope, { title: 'New' });
      expect(fresh.capability).not.toBe(stale.capability);
      expect(fresh.body).toEqual({ title: 'New' });
    } finally {
      vi.useRealTimers();
    }
  });

  it('promotes duplicate successful responses idempotently', () => {
    const store = TestBed.inject(PairMembershipCapabilityStore);
    const pending = store.begin(createScope, { title: 'Pair' });

    const first = store.promote(createScope, sessionId, localPeerId, pending.capability);
    const duplicate = store.promote(createScope, sessionId, localPeerId, pending.capability);

    expect(duplicate).toEqual(first);
  });

  it('never restores a bound proof under another authority or OIDC subject', () => {
    const store = TestBed.inject(PairMembershipCapabilityStore);
    const pending = store.begin(createScope, { title: 'Pair' });
    store.promote(createScope, sessionId, localPeerId, pending.capability);

    expect(() => store.require(
      sessionId,
      localPeerId,
      { ...createScope, oidcSubject: 'account-b' },
    )).toThrow('public_membership_capability_binding_invalid');
  });

  it('enumerates the complete exact authority/account scope in stable batch order', () => {
    const store = TestBed.inject(PairMembershipCapabilityStore);
    for (let index = 0; index < MAX_PUBLIC_PAIR_CATALOG_MEMBERSHIPS + 2; index += 1) {
      const id = `session-${String(index).padStart(2, '0')}`;
      const peer = `peer:${index.toString(16).padStart(64, '0')}`;
      const pending = store.begin(createScope, { title: id });
      store.promote(createScope, id, peer, pending.capability);
    }
    const otherScope = { ...createScope, kind: 'create' as const, oidcSubject: 'account-b' };
    const other = store.begin(otherScope, { title: 'other-account' });
    store.promote(otherScope, 'session-other', `peer:${'f'.repeat(64)}`, other.capability);

    const listed = store.listBound(createScope);

    expect(listed).toHaveLength(MAX_PUBLIC_PAIR_CATALOG_MEMBERSHIPS + 2);
    expect(listed.map(item => item.sessionId)).toEqual(
      Array.from({ length: MAX_PUBLIC_PAIR_CATALOG_MEMBERSHIPS + 2 }, (_, index) => (
        `session-${String(index).padStart(2, '0')}`
      )),
    );
    expect(listed.every(item => item.oidcSubject === 'account-a')).toBe(true);
    expect(Object.isFrozen(listed)).toBe(true);
  });

  it('returns an empty proof batch for a different same-origin account', () => {
    const store = TestBed.inject(PairMembershipCapabilityStore);
    const pending = store.begin(createScope, { title: 'Pair' });
    store.promote(createScope, sessionId, localPeerId, pending.capability);

    expect(store.listBound({ ...createScope, oidcSubject: 'account-b' })).toEqual([]);
  });
});
