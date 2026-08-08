import { Injectable, inject } from '@angular/core';

import { NetworkProfileService } from './network-profile.service';
import type { PairSessionBinding } from './pair-session-binding.store';
import {
  PUBLIC_OIDC_CLIENT_ID,
  PUBLIC_OIDC_ISSUER,
  PUBLIC_WEBRTC_BASE_URL,
  PUBLIC_WEBRTC_SIGNALING_URL,
} from './public-ananta-endpoints';
import { UserAuthService } from './user-auth.service';

export interface PublicPairAuthority {
  readonly baseUrl: string;
  readonly token: string;
  readonly oidcIssuer: string;
  readonly oidcSubject: string;
  readonly profileId: string;
}

export interface PublicOidcLoginAuthority {
  readonly issuer: string;
  readonly clientId: string;
}

const PINNED_PUBLIC_OIDC_AUTHORITY: PublicOidcLoginAuthority = Object.freeze({
  issuer: PUBLIC_OIDC_ISSUER,
  clientId: PUBLIC_OIDC_CLIENT_ID,
});

/** Owns public endpoint/identity trust decisions; it performs no API calls. */
@Injectable({ providedIn: 'root' })
export class PairPublicAuthorityPolicy {
  private readonly profiles = inject(NetworkProfileService);
  private readonly auth = inject(UserAuthService);

  readonly signalingUrl = PUBLIC_WEBRTC_SIGNALING_URL;

  get selected(): boolean {
    const profile = this.profiles.current;
    return profile.profile_id === 'public-ananta' && this.profiles.publicPairOptedIn === true;
  }

  get ready(): boolean {
    if (!this.selected) return false;
    try { this.forNewSession(); return true; } catch { return false; }
  }

  /** Returns only compile-time OIDC coordinates for an explicitly public profile. */
  oidcLoginAuthority(): PublicOidcLoginAuthority | null {
    return this.selected ? PINNED_PUBLIC_OIDC_AUTHORITY : null;
  }

  forNewSession(): PublicPairAuthority | null {
    if (!this.selected) return null;
    const token = this.auth.oidcAccessTokenValue;
    if (!token) throw new Error('public_session_authentication_required');
    if (!this.profileIsTrusted(token)) throw new Error('public_rendezvous_profile_untrusted');
    return {
      baseUrl: trustedPublicBaseUrl(),
      token,
      oidcIssuer: PUBLIC_OIDC_ISSUER,
      oidcSubject: tokenSubject(token),
      profileId: this.profiles.current.profile_id,
    };
  }

  require(binding: PairSessionBinding): PublicPairAuthority {
    if (binding.kind !== 'public') throw new Error('public_session_binding_required');
    if (binding.baseUrl !== trustedPublicBaseUrl() || binding.oidcIssuer !== PUBLIC_OIDC_ISSUER) {
      throw new Error('public_session_binding_untrusted');
    }
    const token = this.auth.oidcAccessTokenValue;
    if (!token) throw new Error('public_session_authentication_lost');
    if (tokenSubject(token) !== binding.oidcSubject) {
      throw new Error('public_session_identity_changed');
    }
    if (!this.selected || !this.profileIsTrusted(token) || this.profiles.current.profile_id !== binding.profileId) {
      throw new Error('public_session_profile_changed');
    }
    return {
      baseUrl: binding.baseUrl,
      token,
      oidcIssuer: binding.oidcIssuer,
      oidcSubject: binding.oidcSubject,
      profileId: binding.profileId || '',
    };
  }

  private profileIsTrusted(token: string): boolean {
    const profile = this.profiles.current;
    const configuredBase = String(profile.rendezvous?.base_url || '').trim();
    const configuredIssuer = String(profile.oidc?.issuer || '').replace(/\/$/, '');
    const claims = decodeJwtPayload(token);
    const tokenIssuer = String(claims?.['iss'] || '').replace(/\/$/, '');
    const subject = typeof claims?.['sub'] === 'string' ? claims['sub'].trim() : '';
    const expiresAt = Number(claims?.['exp']);
    const notBefore = claims?.['nbf'] === undefined ? 0 : Number(claims['nbf']);
    const now = Date.now() / 1000;
    return configuredBaseUrl(configuredBase) === trustedPublicBaseUrl()
      && configuredIssuer === PUBLIC_OIDC_ISSUER
      && profile.require_e2e_payload_encryption === true
      && webrtcOnly(profile.transport_order)
      && webrtcOnly(profile.rendezvous?.transport_order)
      && tokenIssuer === PUBLIC_OIDC_ISSUER
      && subject.length > 0 && subject.length <= 256
      && Number.isFinite(expiresAt) && expiresAt > now
      && Number.isFinite(notBefore) && notBefore <= now + 30;
  }
}

function webrtcOnly(value: unknown): boolean {
  return Array.isArray(value) && value.length === 1 && value[0] === 'webrtc';
}

function trustedPublicBaseUrl(): string {
  const url = new URL(PUBLIC_WEBRTC_BASE_URL);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('public_rendezvous_constant_invalid');
  }
  return url.origin;
}

function configuredBaseUrl(value: string): string {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) return '';
    if (url.pathname !== '/' && url.pathname !== '') return '';
    return url.origin;
  } catch {
    return '';
  }
}

function decodeJwtPayload(token: string | null): Record<string, unknown> | null {
  if (!token) return null;
  try {
    const encoded = token.split('.')[1];
    if (!encoded) return null;
    const normalized = encoded.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(encoded.length / 4) * 4, '=');
    return JSON.parse(atob(normalized)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function tokenSubject(token: string): string {
  const subject = decodeJwtPayload(token)?.['sub'];
  if (typeof subject !== 'string' || !subject.trim() || subject.length > 256) {
    throw new Error('public_oidc_subject_invalid');
  }
  return subject.trim();
}
