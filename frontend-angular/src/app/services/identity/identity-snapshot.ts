import type { IdentitySnapshot } from './identity.types';

/**
 * Pure helpers for building, validating and transforming IdentitySnapshot.
 * Kept dependency-free so it can be used in tests, services, and UI.
 */

const REFRESH_BUFFER_SECONDS = 60;

export function buildSnapshot(input: {
  status: IdentitySnapshot['status'];
  token?: string;
  refreshToken?: string;
  subject?: string;
  issuer?: 'hub' | 'oidc';
  expiresAt?: number;
  error?: string;
}): IdentitySnapshot {
  return {
    status: input.status,
    token: input.token,
    refreshToken: input.refreshToken,
    subject: input.subject,
    issuer: input.issuer,
    expiresAt: input.expiresAt,
    refreshAfter: input.expiresAt ? input.expiresAt - REFRESH_BUFFER_SECONDS : undefined,
    error: input.error,
  };
}

export function isSnapshotExpired(snap: IdentitySnapshot, now: number = Date.now() / 1000): boolean {
  if (snap.status !== 'ready') return false;
  if (snap.expiresAt === undefined) return false;
  return snap.expiresAt <= now;
}

export function needsRefresh(snap: IdentitySnapshot, now: number = Date.now() / 1000): boolean {
  if (snap.status !== 'ready') return false;
  if (snap.refreshAfter === undefined) return false;
  return snap.refreshAfter <= now;
}

export function decodeJwtPayload(token: string): { sub?: string; exp?: number; iss?: string } | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const payload = JSON.parse(atob(parts[1]));
    return payload as { sub?: string; exp?: number; iss?: string };
  } catch {
    return null;
  }
}

export function snapshotFromJwt(token: string, refreshToken?: string, issuer?: 'hub' | 'oidc'): IdentitySnapshot {
  const payload = decodeJwtPayload(token);
  if (!payload) {
    return buildSnapshot({ status: 'expired', token, refreshToken, issuer, error: 'JWT decode failed' });
  }
  const now = Date.now() / 1000;
  const expiresAt = payload.exp;
  const expired = expiresAt !== undefined && expiresAt <= now;
  return buildSnapshot({
    status: expired ? 'expired' : 'ready',
    token,
    refreshToken,
    subject: payload.sub,
    issuer,
    expiresAt,
  });
}