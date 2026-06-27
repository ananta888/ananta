/** T17: Loads network profile from Hub API. */
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';
import { ProfileStateService } from './profile-state.service';
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
    bridge_active?: boolean;
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
  oidc: { issuer: PUBLIC_OIDC_ISSUER, client_id: PUBLIC_OIDC_CLIENT_ID, audience: 'ananta-hub', pkce_required: true, bridge_active: false },
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
  private state = inject(ProfileStateService);

  readonly profile$ = new BehaviorSubject<NetworkProfile>(FALLBACK);

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  load(profileId = 'public-ananta'): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<{ ok: boolean; profile: NetworkProfile }>(
      `${url}/api/network-profiles/${profileId}`, url
    ).subscribe({
      next: r => {
        if (r?.profile) {
          this.profile$.next(r.profile);
          // Mirror into the cycle-free ProfileStateService so other
          // services (UserAuthService) can read bridge_active without
          // pulling HubApiCoreService → UserAuthService.
          this.state.setProfile({
            profile_id: r.profile.profile_id,
            oidc: r.profile.oidc
              ? {
                  issuer: r.profile.oidc.issuer,
                  client_id: r.profile.oidc.client_id,
                  audience: r.profile.oidc.audience,
                  pkce_required: r.profile.oidc.pkce_required,
                  bridge_active: r.profile.oidc.bridge_active,
                }
              : undefined,
          });
        }
      },
      error: () => {},
    });
  }

  get current(): NetworkProfile { return this.profile$.value; }
}
