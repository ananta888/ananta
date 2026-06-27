import type { BridgeRule } from './identity.types';

/**
 * Active profile name. Set by NetworkProfileService.activeProfile$.
 * 'public-ananta' is the public-keycloak bridged profile.
 * 'local', 'enterprise', etc. are direct-hub profiles (no OIDC).
 */
export interface ActiveProfile {
  readonly id: string;
}

/**
 * Bridge rules — single source of truth for cross-sphere token exchange.
 *
 * Today there is exactly ONE rule: public-ananta profile maps an OIDC
 * access-token to a Hub access-token via /auth/oidc/exchange.
 *
 * Other profiles (local, enterprise) have NO bridge rule — they talk to the
 * hub directly with the Hub-issued JWT.
 */
export const BRIDGE_RULES: readonly BridgeRule[] = [
  {
    id: 'public-ananta.oidc-to-hub',
    from: 'oidc',
    to: 'hub',
    when: (ctx) => ctx.activeProfile === 'public-ananta' && ctx.hubUrl().length > 0,
    exchange: async (snapshot, ctx) => {
      if (!snapshot.token) throw new Error('No OIDC token to bridge');
      const url = `${ctx.hubUrl().replace(/\/$/, '')}/auth/oidc/exchange`;
      const r = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${snapshot.token}`,
        },
        body: JSON.stringify({}),
      });
      if (!r.ok) {
        throw new Error(`Bridge exchange failed: HTTP ${r.status}`);
      }
      const data = await r.json();
      // Response may be wrapped in ApiResponse envelope or be raw
      const payload = data?.data ?? data;
      return {
        access_token: payload.access_token,
        refresh_token: payload.refresh_token,
        expires_in: payload.expires_in,
      };
    },
  },
];