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
  warning: string;
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
      this.profile$.next(r.profile);
    } catch {
      // The public fallback remains usable when the protected profile
      // endpoint is unavailable before Hub login.
    }
  }

  get current(): NetworkProfile { return this.profile$.value; }
}
