import { describe, it, expect } from 'vitest';
import {
  buildSnapshot,
  isSnapshotExpired,
  needsRefresh,
  decodeJwtPayload,
  snapshotFromJwt,
} from './identity-snapshot';

/** Generate a JWT with payload for testing */
function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = btoa(JSON.stringify(payload));
  const signature = 'sig';
  return `${header}.${body}.${signature}`;
}

describe('identity-snapshot helpers', () => {
  describe('buildSnapshot', () => {
    it('produces snapshot with status only when no other fields', () => {
      const s = buildSnapshot({ status: 'absent' });
      expect(s.status).toBe('absent');
      expect(s.refreshAfter).toBeUndefined();
    });

    it('computes refreshAfter as expiresAt - 60s', () => {
      const s = buildSnapshot({ status: 'ready', expiresAt: 1000 });
      expect(s.refreshAfter).toBe(940);
    });

    it('omits refreshAfter when no expiresAt given', () => {
      const s = buildSnapshot({ status: 'ready' });
      expect(s.refreshAfter).toBeUndefined();
    });
  });

  describe('isSnapshotExpired', () => {
    it('returns false for non-ready snapshots', () => {
      expect(isSnapshotExpired({ status: 'absent', expiresAt: 0 }, 100)).toBe(false);
      expect(isSnapshotExpired({ status: 'authenticating', expiresAt: 0 }, 100)).toBe(false);
      expect(isSnapshotExpired({ status: 'expired', expiresAt: 0 }, 100)).toBe(false);
    });

    it('returns false when no expiresAt is set', () => {
      expect(isSnapshotExpired({ status: 'ready' }, 100)).toBe(false);
    });

    it('returns true when expiresAt is in the past', () => {
      expect(isSnapshotExpired({ status: 'ready', expiresAt: 99 }, 100)).toBe(true);
    });

    it('returns false when expiresAt is in the future', () => {
      expect(isSnapshotExpired({ status: 'ready', expiresAt: 101 }, 100)).toBe(false);
    });

    it('treats expiresAt == now as expired', () => {
      expect(isSnapshotExpired({ status: 'ready', expiresAt: 100 }, 100)).toBe(true);
    });
  });

  describe('needsRefresh', () => {
    it('returns false for non-ready snapshots', () => {
      expect(needsRefresh({ status: 'absent' }, 100)).toBe(false);
    });

    it('returns false when no refreshAfter set', () => {
      expect(needsRefresh({ status: 'ready' }, 100)).toBe(false);
    });

    it('returns true when now > refreshAfter', () => {
      const snap = buildSnapshot({ status: 'ready', expiresAt: 200 });
      // refreshAfter = 140
      expect(needsRefresh(snap, 141)).toBe(true);
    });

    it('returns false when now <= refreshAfter', () => {
      const snap = buildSnapshot({ status: 'ready', expiresAt: 200 });
      // refreshAfter = 140
      expect(needsRefresh(snap, 139)).toBe(false);
    });
  });

  describe('decodeJwtPayload', () => {
    it('returns null for non-JWT strings', () => {
      expect(decodeJwtPayload('not-a-jwt')).toBeNull();
      expect(decodeJwtPayload('only.two')).toBeNull();
    });

    it('returns payload for valid JWT', () => {
      const token = makeJwt({ sub: 'user-42', exp: 1234567890, iss: 'hub' });
      const payload = decodeJwtPayload(token);
      expect(payload).toEqual({ sub: 'user-42', exp: 1234567890, iss: 'hub' });
    });

    it('returns null when payload is not valid base64', () => {
      expect(decodeJwtPayload('aaa.!!!.ccc')).toBeNull();
    });
  });

  describe('snapshotFromJwt', () => {
    it('produces ready snapshot for non-expired JWT', () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      const token = makeJwt({ sub: 'alice', exp: future });
      const s = snapshotFromJwt(token, 'rt', 'hub');
      expect(s.status).toBe('ready');
      expect(s.subject).toBe('alice');
      expect(s.expiresAt).toBe(future);
      expect(s.refreshAfter).toBe(future - 60);
      expect(s.issuer).toBe('hub');
    });

    it('produces expired snapshot for past-dated JWT', () => {
      const past = Math.floor(Date.now() / 1000) - 100;
      const token = makeJwt({ sub: 'alice', exp: past });
      const s = snapshotFromJwt(token);
      expect(s.status).toBe('expired');
    });

    it('produces expired snapshot when payload cannot be decoded', () => {
      const s = snapshotFromJwt('not-a-jwt');
      expect(s.status).toBe('expired');
      expect(s.error).toBeTruthy();
    });
  });
});