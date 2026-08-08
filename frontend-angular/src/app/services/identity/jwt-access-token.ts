export type JwtAccessTokenFailure =
  | 'missing'
  | 'malformed'
  | 'issuer_invalid'
  | 'subject_invalid'
  | 'expiration_invalid'
  | 'expired'
  | 'not_before_invalid'
  | 'not_yet_valid';

export type JwtAccessTokenInspection =
  | {
      readonly ok: true;
      readonly issuer: string;
      readonly subject: string;
      readonly expiresAt: number;
    }
  | { readonly ok: false; readonly reason: JwtAccessTokenFailure };

/**
 * Performs the browser-side shape and lifetime checks needed before a token is
 * treated as an active identity. Cryptographic verification remains the
 * responsibility of the receiving server.
 */
export function inspectJwtAccessToken(
  token: string | null,
  nowSeconds = Date.now() / 1000,
  notBeforeClockSkewSeconds = 30,
): JwtAccessTokenInspection {
  if (!token) return { ok: false, reason: 'missing' };
  const claims = decodeJwtPayload(token);
  if (!claims) return { ok: false, reason: 'malformed' };

  const issuer = typeof claims['iss'] === 'string' ? claims['iss'].trim().replace(/\/$/, '') : '';
  if (!issuer) return { ok: false, reason: 'issuer_invalid' };

  const subject = typeof claims['sub'] === 'string' ? claims['sub'].trim() : '';
  if (!subject || subject.length > 256) return { ok: false, reason: 'subject_invalid' };

  const expiresAt = claims['exp'];
  if (typeof expiresAt !== 'number' || !Number.isFinite(expiresAt)) {
    return { ok: false, reason: 'expiration_invalid' };
  }
  if (expiresAt <= nowSeconds) return { ok: false, reason: 'expired' };

  if (claims['nbf'] !== undefined) {
    const notBefore = claims['nbf'];
    if (typeof notBefore !== 'number' || !Number.isFinite(notBefore)) {
      return { ok: false, reason: 'not_before_invalid' };
    }
    if (notBefore > nowSeconds + notBeforeClockSkewSeconds) {
      return { ok: false, reason: 'not_yet_valid' };
    }
  }

  return { ok: true, issuer, subject, expiresAt };
}

export function isJwtAccessTokenCurrent(token: string | null, nowSeconds = Date.now() / 1000): boolean {
  return inspectJwtAccessToken(token, nowSeconds).ok;
}

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3 || parts.some((part) => !part)) return null;
    const normalized = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
    const decoded = JSON.parse(new TextDecoder().decode(bytes));
    return decoded && typeof decoded === 'object' && !Array.isArray(decoded)
      ? decoded as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}
