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
      hubLinkEnabled:
        this.profiles.current?.oidc?.hub_link_enabled === true
        || this.profiles.current?.oidc?.bridge_active === true,
      hubUrl,
    };
  }

  /**
   * Whether optional linked-account exchange is configured.
   * This does not choose which login form is visible: Hub and Pair login
   * remain independent and may both be shown.
   */
  mode(): 'oidc-bridge' | 'hub-direct' {
    const profile = this.profiles.current;
    if (
      profile?.oidc?.hub_link_enabled === true
      || profile?.oidc?.bridge_active === true
    ) {
      return 'oidc-bridge';
    }
    return 'hub-direct';
  }

  /** Pair/WebRTC login is independent from Hub login. */
  get showOidcLogin(): boolean {
    const oidc = this.profiles.current?.oidc;
    return oidc?.enabled === true || Boolean(oidc?.issuer && oidc?.client_id);
  }

  /** Hub login is always available and remains the worker access authority. */
  get showHubDirectLogin(): boolean {
    return true;
  }

  get hubLinkEnabled(): boolean {
    return this.mode() === 'oidc-bridge';
  }

  /**
   * Whether the "Bei Keycloak registrieren" button should be shown.
   *
   * Single source of truth = the backend-supplied
   * `oidc.registration_allowed` flag (set in /api/network-profiles from
   * settings.OIDC_REGISTRATION_ALLOWED AND oidc_is_configured()).
   *
   * Frontend-side defensive invariant: button only renders when the
   * keycloak-realm is reachable via the Pair config AND a hub agent is
   * registered locally (otherwise there is no keycloak-realm to register
   * against from this device).
   *
   * Default-deny: if the flag is missing or false → false.
   */
  get showRegistration(): boolean {
    const oidc = this.profiles.current?.oidc;
    if (!oidc?.registration_allowed) return false;
    if (!oidc.issuer || !oidc.client_id) return false;
    const hub = this.dir.list().find((a) => a.role === 'hub');
    return !!hub?.url;
  }
}
