/** T17: Loads network profile from Hub API. */
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, firstValueFrom } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';
import {
  PUBLIC_OIDC_CLIENT_ID,
  PUBLIC_OIDC_ISSUER,
  PUBLIC_WEBRTC_BASE_URL,
  PUBLIC_WEBRTC_SIGNALING_URL,
  PUBLIC_WEBRTC_STUN_URL,
} from './public-ananta-endpoints';

export interface NetworkProfile {
  profile_id: string;
  public_rendezvous?: boolean;
  label: string;
  oidc: {
    issuer: string;
    client_id: string;
    audience: string;
    pkce_required: boolean;
    enabled?: boolean;
    hub_link_enabled?: boolean;
    bridge_active?: boolean;
    registration_allowed?: boolean;
  };
  rendezvous: { base_url: string; signaling_url: string; transport_order: string[] };
  ice_servers: RTCIceServer[];
  require_e2e_payload_encryption: boolean;
  signaling_url: string;
  transport_order: string[];
  semantic_media_feature_flags: SemanticMediaFeatureFlags;
  sfu_broadcast_feature_version?: number;
  sfu_broadcast_feature_available?: boolean;
  sfu_broadcast_reason_codes?: readonly string[];
  warning: string;
}

export interface SemanticMediaFeatureFlags {
  ordinary_media_publication: boolean;
  semantic_visual_capture: boolean;
  semantic_speech_runtime: boolean;
  semantic_media_sfu: boolean;
  semantic_media_broadcast: boolean;
  semantic_media_receiver_groups: boolean;
  semantic_media_fleet_admission: boolean;
  semantic_media_turn_cost_controls: boolean;
  semantic_media_background_operations: boolean;
  peer_evidence_sync: boolean;
  speech_reconciliation: boolean;
  speech_adaptation_training: boolean;
  speech_adapter_routing: boolean;
}

export const SEMANTIC_MEDIA_FEATURE_DEFAULTS: Readonly<SemanticMediaFeatureFlags> = Object.freeze({
  ordinary_media_publication: false,
  semantic_visual_capture: false,
  semantic_speech_runtime: false,
  semantic_media_sfu: false,
  semantic_media_broadcast: false,
  semantic_media_receiver_groups: false,
  semantic_media_fleet_admission: false,
  semantic_media_turn_cost_controls: false,
  semantic_media_background_operations: false,
  peer_evidence_sync: false,
  speech_reconciliation: false,
  speech_adaptation_training: false,
  speech_adapter_routing: false,
});

export function normalizeSemanticMediaFeatureFlags(value: unknown): SemanticMediaFeatureFlags {
  const candidate = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const requested = (key: keyof SemanticMediaFeatureFlags): boolean => candidate[key] === true;
  const background = requested('semantic_media_background_operations');
  const speech = requested('semantic_speech_runtime');
  const broadcast = requested('semantic_media_broadcast');
  const evidence = requested('peer_evidence_sync') && speech && background;
  const reconciliation = requested('speech_reconciliation') && evidence && background;
  const training = requested('speech_adaptation_training') && reconciliation && background;
  return {
    ordinary_media_publication: requested('ordinary_media_publication'),
    semantic_visual_capture: requested('semantic_visual_capture'),
    semantic_speech_runtime: speech,
    semantic_media_sfu: requested('semantic_media_sfu'),
    semantic_media_broadcast: broadcast,
    semantic_media_receiver_groups: requested('semantic_media_receiver_groups') && broadcast,
    semantic_media_fleet_admission: requested('semantic_media_fleet_admission') && broadcast,
    semantic_media_turn_cost_controls: requested('semantic_media_turn_cost_controls') && broadcast,
    semantic_media_background_operations: background,
    peer_evidence_sync: evidence,
    speech_reconciliation: reconciliation,
    speech_adaptation_training: training,
    speech_adapter_routing: requested('speech_adapter_routing') && training && background,
  };
}

function normalizeBroadcastFeatureVersion(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

function normalizeBroadcastReasonCodes(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter(item => typeof item === 'string' && item.length <= 128))];
}

/** Drop malformed ICE entries before they reach the browser constructor. */
export function normalizeIceServers(value: unknown): RTCIceServer[] {
  if (!Array.isArray(value)) return [];
  const normalized: RTCIceServer[] = [];
  for (const candidate of value) {
    if (!candidate || typeof candidate !== 'object') continue;
    const server = candidate as RTCIceServer;
    const rawUrls = typeof server.urls === 'string' ? [server.urls] : server.urls;
    if (!Array.isArray(rawUrls)) continue;
    const hasTurnCredential = typeof server.username === 'string'
      && server.username.length > 0
      && typeof server.credential === 'string'
      && server.credential.length > 0;
    const urls = rawUrls.filter(url => (
      typeof url === 'string'
      && /^(?:stun|stuns|turn|turns):/i.test(url)
      && (!/^turns?:/i.test(url) || hasTurnCredential)
    ));
    if (!urls.length) continue;
    normalized.push({
      ...server,
      urls: typeof server.urls === 'string' ? urls[0] : urls,
    });
  }
  return normalized;
}

/** Public Pair traffic has no application-payload Hub relay capability. */
export function normalizePairTransportOrder(profile: Pick<NetworkProfile, 'profile_id' | 'public_rendezvous' | 'transport_order'>): string[] {
  if (profile.profile_id === 'public-ananta' || profile.public_rendezvous === true) return ['webrtc'];
  const order = Array.isArray(profile.transport_order)
    ? profile.transport_order.filter(item => item === 'webrtc' || item === 'hub_relay')
    : [];
  return [...new Set(order)];
}

const PUBLIC_PROFILE_ID = 'public-ananta';
const LOCAL_PROFILE_ID = 'local';
const PROFILE_SELECTION_STORAGE_KEY = 'ananta.network-profile-selection.v1';

const PUBLIC_FALLBACK: NetworkProfile = {
  profile_id: 'public-ananta',
  label: 'Public Ananta (fallback)',
  oidc: {
    issuer: PUBLIC_OIDC_ISSUER,
    client_id: PUBLIC_OIDC_CLIENT_ID,
    audience: 'ananta-hub',
    pkce_required: true,
    enabled: true,
    hub_link_enabled: false,
    bridge_active: false,
    registration_allowed: false,
  },
  public_rendezvous: true,
  rendezvous: { base_url: PUBLIC_WEBRTC_BASE_URL, signaling_url: PUBLIC_WEBRTC_SIGNALING_URL, transport_order: ['webrtc'] },
  ice_servers: [{ urls: PUBLIC_WEBRTC_STUN_URL }],
  require_e2e_payload_encryption: true,
  signaling_url: PUBLIC_WEBRTC_SIGNALING_URL,
  transport_order: ['webrtc'],
  semantic_media_feature_flags: { ...SEMANTIC_MEDIA_FEATURE_DEFAULTS },
  sfu_broadcast_feature_version: 0,
  sfu_broadcast_feature_available: false,
  sfu_broadcast_reason_codes: [],
  warning: '',
};

const LOCAL_FALLBACK: NetworkProfile = {
  profile_id: LOCAL_PROFILE_ID,
  label: 'Local / Private Deployment',
  oidc: {
    issuer: '',
    client_id: '',
    audience: 'ananta-hub',
    pkce_required: true,
    enabled: false,
    hub_link_enabled: false,
    bridge_active: false,
    registration_allowed: false,
  },
  public_rendezvous: false,
  rendezvous: { base_url: '', signaling_url: '', transport_order: ['hub_relay'] },
  ice_servers: [],
  require_e2e_payload_encryption: false,
  signaling_url: '',
  transport_order: ['hub_relay'],
  semantic_media_feature_flags: { ...SEMANTIC_MEDIA_FEATURE_DEFAULTS },
  sfu_broadcast_feature_version: 0,
  sfu_broadcast_feature_available: false,
  sfu_broadcast_reason_codes: [],
  warning: '',
};

@Injectable({ providedIn: 'root' })
export class NetworkProfileService {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);
  private publicPairEnabledForWindow = this.readPersistedPublicOptIn();

  readonly profile$ = new BehaviorSubject<NetworkProfile>(LOCAL_FALLBACK);

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  /**
   * Restore the persisted selection, defaulting to the private/local profile.
   * Passing a logical context remains backward compatible for existing callers,
   * but cannot activate the public profile without prior user opt-in.
   */
  async load(profileId = this.persistedProfileId): Promise<void> {
    const effectiveProfileId = profileId === PUBLIC_PROFILE_ID && !this.publicPairOptedIn
      ? LOCAL_PROFILE_ID
      : profileId;
    if (effectiveProfileId === PUBLIC_PROFILE_ID) {
      this.profile$.next(PUBLIC_FALLBACK);
      // The public Pair authority is compile-time pinned and intentionally
      // independent from Hub authentication or mutable Hub projections.
      return;
    } else if (effectiveProfileId === LOCAL_PROFILE_ID) {
      this.profile$.next(LOCAL_FALLBACK);
    }
    const url = this.hubUrl;
    if (!url) return;
    try {
      const r = await firstValueFrom(this.core.get<{ ok: boolean; profile: NetworkProfile }>(
        `${url}/api/network-profiles/${effectiveProfileId}`, url
      ));
      if (!r?.profile || r.profile.profile_id !== effectiveProfileId) return;
      const transportOrder = normalizePairTransportOrder(r.profile);
      const semanticMediaFlags = effectiveProfileId === PUBLIC_PROFILE_ID
        ? { ...SEMANTIC_MEDIA_FEATURE_DEFAULTS }
        : normalizeSemanticMediaFeatureFlags(r.profile.semantic_media_feature_flags);
      this.profile$.next({
        ...r.profile,
        rendezvous: { ...r.profile.rendezvous, transport_order: transportOrder },
        transport_order: transportOrder,
        ice_servers: normalizeIceServers(r.profile.ice_servers),
        semantic_media_feature_flags: semanticMediaFlags,
        sfu_broadcast_feature_version: normalizeBroadcastFeatureVersion(
          r.profile.sfu_broadcast_feature_version
        ),
        sfu_broadcast_feature_available: r.profile.sfu_broadcast_feature_available === true,
        sfu_broadcast_reason_codes: normalizeBroadcastReasonCodes(
          r.profile.sfu_broadcast_reason_codes
        ),
      });
    } catch {
      // The explicitly selected fallback remains usable when the protected
      // profile endpoint is unavailable before Hub login.
    }
  }

  /** Persist the user's explicit decision to use the public Pair authority. */
  async enablePublicPair(): Promise<void> {
    this.publicPairEnabledForWindow = true;
    this.persistProfileId(PUBLIC_PROFILE_ID);
    this.profile$.next(PUBLIC_FALLBACK);
    await this.load(PUBLIC_PROFILE_ID);
  }

  /** Return to the private/local authority and remove the public opt-in. */
  async useLocalProfile(): Promise<void> {
    this.publicPairEnabledForWindow = false;
    this.persistProfileId(LOCAL_PROFILE_ID);
    this.profile$.next(LOCAL_FALLBACK);
    await this.load(LOCAL_PROFILE_ID);
  }

  get publicPairOptedIn(): boolean {
    return this.publicPairEnabledForWindow;
  }

  private get persistedProfileId(): string {
    return this.publicPairEnabledForWindow ? PUBLIC_PROFILE_ID : LOCAL_PROFILE_ID;
  }

  private readPersistedPublicOptIn(): boolean {
    try {
      return localStorage.getItem(PROFILE_SELECTION_STORAGE_KEY) === PUBLIC_PROFILE_ID;
    } catch {
      return false;
    }
  }

  private persistProfileId(profileId: typeof PUBLIC_PROFILE_ID | typeof LOCAL_PROFILE_ID): void {
    try {
      if (profileId === PUBLIC_PROFILE_ID) {
        localStorage.setItem(PROFILE_SELECTION_STORAGE_KEY, profileId);
      } else {
        localStorage.removeItem(PROFILE_SELECTION_STORAGE_KEY);
      }
    } catch {
      // Storage can be unavailable in privacy-restricted webviews. The
      // in-memory selection above remains valid for the current window.
    }
  }

  get current(): NetworkProfile { return this.profile$.value; }
}
