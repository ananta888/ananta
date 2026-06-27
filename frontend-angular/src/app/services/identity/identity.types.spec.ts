import { describe, it, expect } from 'vitest';
import type { IdentitySphere, IdentityStatus, BridgeRule, BridgeContext } from './identity.types';

describe('identity.types', () => {
  it('IdentitySphere is a union of three string literals', () => {
    const valid: IdentitySphere[] = ['hub', 'oidc', 'signaling'];
    expect(valid).toHaveLength(3);
  });

  it('IdentityStatus has four lifecycle states', () => {
    const valid: IdentityStatus[] = ['absent', 'authenticating', 'ready', 'expired'];
    expect(valid).toHaveLength(4);
  });

  it('IdentitySnapshot has required status field', () => {
    const snap = { status: 'absent' as const };
    expect(snap.status).toBe('absent');
  });

  it('BridgeRule has from/to/when/exchange fields', () => {
    const rule: BridgeRule = {
      id: 'test-rule',
      from: 'oidc',
      to: 'hub',
      when: (ctx: BridgeContext) => ctx.activeProfile === 'public-ananta',
      exchange: async () => ({ access_token: 'x' }),
    };
    expect(rule.from).toBe('oidc');
    expect(rule.to).toBe('hub');
    expect(rule.when({ activeProfile: 'public-ananta', hubUrl: () => '' })).toBe(true);
    expect(rule.when({ activeProfile: 'local', hubUrl: () => '' })).toBe(false);
  });

  it('BridgeError extends Error with code field', () => {
    const err = new (class extends Error {
      constructor(public code: string, message: string) {
        super(message);
        this.name = 'BridgeError';
      }
    })('exchange_failed', 'HTTP 500');
    expect(err.code).toBe('exchange_failed');
    expect(err.message).toBe('HTTP 500');
  });
});
