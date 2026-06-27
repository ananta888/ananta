import { describe, it, expect, beforeEach } from 'vitest';
import {
  IDENTITY_STORAGE_LAYOUT,
  allIdentityKeys,
  readIdentityKey,
  clearAllIdentityStorage,
} from './identity-storage-layout';

describe('identity-storage-layout', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('declares hub access token as plaintext', () => {
    expect(IDENTITY_STORAGE_LAYOUT.hub.accessToken.encryption).toBe('plaintext');
    expect(IDENTITY_STORAGE_LAYOUT.hub.accessToken.key).toBe('ananta.user.token');
  });

  it('declares hub refresh token as encrypted', () => {
    expect(IDENTITY_STORAGE_LAYOUT.hub.refreshToken.encryption).toBe('encrypted');
    expect(IDENTITY_STORAGE_LAYOUT.hub.refreshToken.key).toBe('ananta.hub.refresh_token');
  });

  it('declares oidc access token as plaintext', () => {
    expect(IDENTITY_STORAGE_LAYOUT.oidc.accessToken.encryption).toBe('plaintext');
  });

  it('declares oidc refresh token as encrypted', () => {
    expect(IDENTITY_STORAGE_LAYOUT.oidc.refreshToken.encryption).toBe('encrypted');
  });

  it('allIdentityKeys returns 5 known keys (hub.at, hub.rt, oidc.at, oidc.rt, legacy)', () => {
    expect(allIdentityKeys()).toHaveLength(5);
  });

  it('allIdentityKeys includes legacy hub RT key', () => {
    const keys = allIdentityKeys().map((k) => k.key);
    expect(keys).toContain('ananta.user.refresh_token');
  });

  it('readIdentityKey returns null for missing entry', () => {
    expect(readIdentityKey('hub', 'accessToken')).toBeNull();
  });

  it('readIdentityKey reads back what was written', () => {
    localStorage.setItem('ananta.user.token', 'jwt-abc');
    expect(readIdentityKey('hub', 'accessToken')).toBe('jwt-abc');
  });

  it('readIdentityKey returns null for unknown sphere', () => {
    expect(readIdentityKey('unknown' as any, 'accessToken')).toBeNull();
  });

  it('readIdentityKey returns null for unknown field', () => {
    expect(readIdentityKey('hub', 'unknown' as any)).toBeNull();
  });

  it('clearAllIdentityStorage removes all known keys', () => {
    localStorage.setItem('ananta.user.token', 'a');
    localStorage.setItem('ananta.hub.refresh_token', 'b');
    localStorage.setItem('ananta.oidc.access_token', 'c');
    localStorage.setItem('ananta.oidc.refresh_token', 'd');
    localStorage.setItem('ananta.user.refresh_token', 'e');
    localStorage.setItem('ananta.some.other', 'f');

    clearAllIdentityStorage();

    expect(localStorage.getItem('ananta.user.token')).toBeNull();
    expect(localStorage.getItem('ananta.hub.refresh_token')).toBeNull();
    expect(localStorage.getItem('ananta.oidc.access_token')).toBeNull();
    expect(localStorage.getItem('ananta.oidc.refresh_token')).toBeNull();
    expect(localStorage.getItem('ananta.user.refresh_token')).toBeNull();
    // non-identity keys are preserved
    expect(localStorage.getItem('ananta.some.other')).toBe('f');
  });
});