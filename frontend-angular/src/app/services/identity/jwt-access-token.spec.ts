import { describe, expect, it } from 'vitest';

import { inspectJwtAccessToken, isJwtAccessTokenCurrent } from './jwt-access-token';

function jwt(payload: Record<string, unknown>): string {
  const encoded = btoa(JSON.stringify(payload))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `header.${encoded}.signature`;
}

describe('inspectJwtAccessToken', () => {
  const now = 1_750_000_000;

  it('accepts a current token with bounded identity claims', () => {
    const result = inspectJwtAccessToken(jwt({
      iss: 'https://issuer.test/', sub: 'user-1', exp: now + 60, nbf: now + 30,
    }), now);

    expect(result).toMatchObject({
      ok: true,
      issuer: 'https://issuer.test',
      subject: 'user-1',
      expiresAt: now + 60,
    });
  });

  it.each([
    [null, 'missing'],
    ['broken', 'malformed'],
    [jwt({ sub: 'user-1', exp: now + 60 }), 'issuer_invalid'],
    [jwt({ iss: 'https://issuer.test', exp: now + 60 }), 'subject_invalid'],
    [jwt({ iss: 'https://issuer.test', sub: 'user-1' }), 'expiration_invalid'],
    [jwt({ iss: 'https://issuer.test', sub: 'user-1', exp: String(now + 60) }), 'expiration_invalid'],
    [jwt({ iss: 'https://issuer.test', sub: 'user-1', exp: now }), 'expired'],
    [jwt({ iss: 'https://issuer.test', sub: 'user-1', exp: now + 60, nbf: 'later' }), 'not_before_invalid'],
    [jwt({ iss: 'https://issuer.test', sub: 'user-1', exp: now + 60, nbf: now + 31 }), 'not_yet_valid'],
  ])('rejects %s as %s', (token, reason) => {
    expect(inspectJwtAccessToken(token, now)).toEqual({ ok: false, reason });
  });

  it('exposes a narrow boolean helper for login indicators', () => {
    expect(isJwtAccessTokenCurrent(jwt({
      iss: 'https://issuer.test', sub: 'user-1', exp: now + 1,
    }), now)).toBe(true);
    expect(isJwtAccessTokenCurrent(jwt({
      iss: 'https://issuer.test', sub: 'user-1', exp: now,
    }), now)).toBe(false);
  });
});
