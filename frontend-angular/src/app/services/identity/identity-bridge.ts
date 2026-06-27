import { Injectable, inject } from '@angular/core';
import type { BridgeContext, BridgeRule, IdentitySphere } from './identity.types';
import { BRIDGE_RULES } from './identity-bridge.config';
import { NetworkProfileService } from '../network-profile.service';
import { AgentDirectoryService } from '../agent-directory.service';

/**
 * IdentityBridge — applies BRIDGE_RULES in a configurable order.
 *
 * On a successful OIDC login (e.g. profile 'public-ananta'), this service
 * is asked to bridge: take the OIDC snapshot and produce a Hub snapshot
 * via the appropriate BridgeRule.
 */
@Injectable({ providedIn: 'root' })
export class IdentityBridge {
  private readonly profiles = inject(NetworkProfileService);
  private readonly dir = inject(AgentDirectoryService);

  /**
   * Find all rules that should fire for the given `from` sphere
   * and the current bridge context.
   */
  findApplicableRules(from: IdentitySphere, ctx?: BridgeContext): BridgeRule[] {
    const effectiveCtx = ctx ?? this.buildContext();
    return BRIDGE_RULES.filter(
      (rule) => rule.from === from && rule.when(effectiveCtx),
    );
  }

  buildContext(): BridgeContext {
    const hubUrl = () => {
      const hub = this.dir.list().find((a) => a.role === 'hub');
      return hub?.url ?? '';
    };
    return {
      activeProfile: this.profiles.current?.profile_id ?? '',
      hubUrl,
    };
  }

  /**
   * Login mode based on the active network profile.
   *   - 'oidc-bridge'   OIDC + bridge to hub  (e.g. public-ananta OR
   *                     Hub-OIDC-Settings enabled, see network-profile.service)
   *   - 'hub-direct'    direct hub login       (e.g. local / enterprise)
   *
   * Welle 4: The profile's `oidc.bridge_active` flag is the source of truth
   * for whether the Hub has explicitly opted into the SSO bridge (set
   * server-side based on OIDC_ENABLED + required fields). Profile_id alone
   * is no longer enough — a 'public-ananta' profile with `bridge_active=false`
   * means the Hub is in legacy mode and the frontend must show the Hub-direct
   * login form.
   */
  mode(): 'oidc-bridge' | 'hub-direct' {
    const profile = this.profiles.current;
    const ctx = this.buildContext();
    if (profile?.oidc?.bridge_active === true) {
      return 'oidc-bridge';
    }
    if (ctx.activeProfile === 'public-ananta' && ctx.hubUrl().length > 0) {
      return 'oidc-bridge';
    }
    return 'hub-direct';
  }

  /** Whether the Keycloak/OIDC button should be shown in the login UI. */
  get showOidcLogin(): boolean {
    return this.mode() === 'oidc-bridge';
  }

  /** Whether username+password (Hub-direct) login should be shown. */
  get showHubDirectLogin(): boolean {
    return this.mode() === 'hub-direct';
  }
}