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
  warning: string;
}

export interface SemanticMediaFeatureFlags {
  ordinary_media_publication: boolean;
  semantic_visual_capture: boolean;
  semantic_speech_runtime: boolean;
  semantic_media_sfu: boolean;
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
  const evidence = requested('peer_evidence_sync') && speech && background;
  const reconciliation = requested('speech_reconciliation') && evidence && background;
  const training = requested('speech_adaptation_training') && reconciliation && background;
  return {
    ordinary_media_publication: requested('ordinary_media_publication'),
    semantic_visual_capture: requested('semantic_visual_capture'),
    semantic_speech_runtime: speech,
    semantic_media_sfu: requested('semantic_media_sfu'),
    semantic_media_background_operations: background,
    peer_evidence_sync: evidence,
    speech_reconciliation: reconciliation,
    speech_adaptation_training: training,
    speech_adapter_routing: requested('speech_adapter_routing') && training && background,
  };
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

const FALLBACK: NetworkProfile = {
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
  rendezvous: { base_url: PUBLIC_WEBRTC_BASE_URL, signaling_url: PUBLIC_WEBRTC_SIGNALING_URL, transport_order: ['webrtc', 'hub_relay'] },
  ice_servers: [{ urls: PUBLIC_WEBRTC_STUN_URL }],
  require_e2e_payload_encryption: true,
  signaling_url: PUBLIC_WEBRTC_SIGNALING_URL,
  transport_order: ['webrtc', 'hub_relay'],
  semantic_media_feature_flags: { ...SEMANTIC_MEDIA_FEATURE_DEFAULTS },
  warning: '',
};

@Injectable({ providedIn: 'root' })
export class NetworkProfileService {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);

  readonly profile$ = new BehaviorSubject<NetworkProfile>(FALLBACK);

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  async load(profileId = 'public-ananta'): Promise<void> {
    const url = this.hubUrl;
    if (!url) return;
    try {
      const r = await firstValueFrom(this.core.get<{ ok: boolean; profile: NetworkProfile }>(
        `${url}/api/network-profiles/${profileId}`, url
      ));
      if (!r?.profile) return;
      this.profile$.next({
        ...r.profile,
        ice_servers: normalizeIceServers(r.profile.ice_servers),
        semantic_media_feature_flags: normalizeSemanticMediaFeatureFlags(
          r.profile.semantic_media_feature_flags
        ),
      });
    } catch {
      // The public fallback remains usable when the protected profile
      // endpoint is unavailable before Hub login.
    }
  }

  get current(): NetworkProfile { return this.profile$.value; }
}
