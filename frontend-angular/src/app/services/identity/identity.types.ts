/**
 * Identity-Sphären — orthogonal, jede mit eigenem Lifecycle.
 * Drei Sphären:
 *   - 'hub'       Ananta-Hub (JWT gegen settings.secret_key)
 *   - 'oidc'      Keycloak / OIDC (gegen keycloak.ananta.de)
 *   - 'signaling' WebRTC Signaling (derived von oidc, kein eigener Lifecycle)
 */
export type IdentitySphere = 'hub' | 'oidc' | 'signaling';

/**
 * Lifecycle-Status einer Identity-Sphäre.
 * absent           — nicht eingeloggt
 * authenticating   — Login läuft gerade
 * ready            — Token gültig, nicht expired
 * expired          — Token expired, Refresh failed oder nicht möglich
 */
export type IdentityStatus =
  | 'absent'
  | 'authenticating'
  | 'ready'
  | 'expired';

export interface IdentitySnapshot {
  status: IdentityStatus;
  /** Bearer-Token (Klartext im Memory, weil für jeden HTTP-Request lesbar) */
  token?: string;
  /** Refresh-Token — verschlüsselt im Storage, hier entschlüsselt im Memory */
  refreshToken?: string;
  /** Subject-Claim (User-ID, Username, E-Mail) */
  subject?: string;
  /** Issuer-Kennung: 'hub' | 'oidc' */
  issuer?: 'hub' | 'oidc';
  /** Unix-Timestamp (Sekunden), wann Token abläuft */
  expiresAt?: number;
  /** expiresAt - 60s Sicherheits-Marge, Trigger-Zeitpunkt für Proactive-Refresh */
  refreshAfter?: number;
  /** Letzter Fehler, falls status='expired' */
  error?: string;
}

/**
 * Quelle einer Identity-Sphäre. Jede Source hält snapshot$,
 * refresh(), logout().
 */
export interface IdentitySource {
  readonly sphere: IdentitySphere;
  readonly snapshot$: import('rxjs').Observable<IdentitySnapshot>;
  refresh(): Promise<void>;
  logout(): void;
}

/**
 * Bridge-Kontext: Daten, die BRIDGE_RULES für ihre `when`-Prädikate brauchen.
 */
export interface BridgeContext {
  /** Aktives Profil-ID aus NetworkProfileService */
  activeProfile: string;
  /** Explicit server capability for OIDC-to-Hub account-link exchange. */
  hubLinkEnabled: boolean;
  /** Resolved Hub-URL (oder leer wenn keiner im Directory) */
  hubUrl(): string;
}

/**
 * Result einer Bridge-Exchange (z.B. /auth/oidc/exchange).
 */
export interface BridgeExchangeResult {
  access_token: string;
  refresh_token?: string;
  expires_in?: number;
}

/**
 * Eine Bridge-Regel: Mappt Token einer Sphäre in eine andere.
 * BRIDGE_RULES ist ein statisches Array in identity-bridge.config.ts.
 */
export interface BridgeRule {
  id: string;
  /** Quell-Sphäre (kann nicht 'signaling' sein — das ist derived) */
  from: Exclude<IdentitySphere, 'signaling'>;
  /** Ziel-Sphäre (kann nicht 'signaling' sein) */
  to: Exclude<IdentitySphere, 'signaling'>;
  /** Wann gilt diese Regel? */
  when: (ctx: BridgeContext) => boolean;
  /** Wie wird der Token ausgetauscht? */
  exchange: (snapshot: IdentitySnapshot, ctx: BridgeContext) => Promise<BridgeExchangeResult>;
}

export class BridgeError extends Error {
  constructor(public code: string, message: string) {
    super(message);
    this.name = 'BridgeError';
  }
}
