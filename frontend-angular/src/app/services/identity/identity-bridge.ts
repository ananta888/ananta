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
   *   - 'oidc-bridge'   OIDC + bridge to hub  (e.g. public-ananta)
   *   - 'hub-direct'    direct hub login       (e.g. local / enterprise)
   */
  mode(): 'oidc-bridge' | 'hub-direct' {
    const ctx = this.buildContext();
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