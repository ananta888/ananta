/**
 * OIDC wire contracts and their validators: discovery metadata pinning,
 * device authorization responses, persisted redirect PKCE transactions,
 * endpoint URL normalization and the PKCE random/challenge encoders.
 * Pure functions only; the OIDC service owns all state and I/O.
 */
import { sha256Bytes } from '../shared/crypto/sha256';
import {
  PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
  PUBLIC_OIDC_CLIENT_ID,
  PUBLIC_OIDC_DEVICE_AUTHORIZATION_ENDPOINT,
  PUBLIC_OIDC_END_SESSION_ENDPOINT,
  PUBLIC_OIDC_ISSUER,
  PUBLIC_OIDC_TOKEN_ENDPOINT,
} from './public-ananta-endpoints';

export interface OidcMeta {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint: string;
  device_authorization_endpoint?: string;
}

export interface DeviceAuthResponse {
  device_code: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete?: string;
  expires_in: number;
  interval: number;
}

export interface DeviceFlowAuthorityBinding {
  readonly tokenEndpoint: string;
  readonly clientId: string;
  readonly expiresAtMs: number;
}

export interface RedirectPkceTransaction {
  readonly verifier: string;
  readonly state: string;
  readonly nonce: string;
  readonly redirectPath: string;
  readonly linkHub: boolean;
  readonly issuer: string;
  readonly clientId: string;
  readonly tokenEndpoint: string;
}

export function validateDeviceAuthResponse(value: unknown): DeviceAuthResponse {
  if (!value || typeof value !== 'object') throw new Error('oidc_device_response_invalid');
  const response = value as Partial<DeviceAuthResponse>;
  const bounded = (candidate: unknown, maxLength: number): candidate is string => (
    typeof candidate === 'string' && candidate.length > 0 && candidate.length <= maxLength
  );
  if (
    !bounded(response.device_code, 2048)
    || !bounded(response.user_code, 256)
    || !bounded(response.verification_uri, 2048)
    || !Number.isSafeInteger(response.expires_in)
    || Number(response.expires_in) <= 0
    || Number(response.expires_in) > 86_400
    || !Number.isSafeInteger(response.interval)
    || Number(response.interval) <= 0
    || Number(response.interval) > 300
  ) throw new Error('oidc_device_response_invalid');
  return Object.freeze({ ...response }) as DeviceAuthResponse;
}

export function assertPinnedPublicMetadata(meta: OidcMeta): OidcMeta {
  if (
    meta.issuer !== PUBLIC_OIDC_ISSUER
    || meta.authorization_endpoint !== PUBLIC_OIDC_AUTHORIZATION_ENDPOINT
    || meta.token_endpoint !== PUBLIC_OIDC_TOKEN_ENDPOINT
    || meta.end_session_endpoint !== PUBLIC_OIDC_END_SESSION_ENDPOINT
    || (
      meta.device_authorization_endpoint !== undefined
      && meta.device_authorization_endpoint !== PUBLIC_OIDC_DEVICE_AUTHORIZATION_ENDPOINT
    )
  ) throw new Error('public_oidc_metadata_untrusted');
  return meta;
}

export function parseRedirectPkceTransaction(raw: string): RedirectPkceTransaction | null {
  try {
    const value = JSON.parse(raw) as Partial<RedirectPkceTransaction>;
    const opaque = (candidate: unknown, maxLength: number): candidate is string => (
      typeof candidate === 'string'
      && /^[A-Za-z0-9_-]+$/.test(candidate)
      && candidate.length <= maxLength
    );
    if (
      !opaque(value.verifier, 256)
      || !opaque(value.state, 256)
      || !opaque(value.nonce, 256)
      || typeof value.redirectPath !== 'string'
      || !value.redirectPath.startsWith('/')
      || value.redirectPath.startsWith('//')
      || value.redirectPath.length > 2048
      || typeof value.linkHub !== 'boolean'
      || value.issuer !== PUBLIC_OIDC_ISSUER
      || value.clientId !== PUBLIC_OIDC_CLIENT_ID
      || value.tokenEndpoint !== PUBLIC_OIDC_TOKEN_ENDPOINT
    ) return null;
    return value as RedirectPkceTransaction;
  } catch { return null; }
}

export function normalizeOidcHttpUrl(value: string, label: string, isIssuer = false): string {
  const candidate = String(value || '').trim().replace(/\/+$/, '');
  if (!candidate) throw new Error(`${label} is missing`);
  const parsed = new URL(candidate);
  const hostname = parsed.hostname.toLowerCase();
  const localhost = hostname === 'localhost'
    || hostname === '127.0.0.1'
    || hostname === '[::1]'
    || hostname.endsWith('.localhost');
  if (parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && localhost)) {
    throw new Error(`${label} must use HTTPS or localhost HTTP`);
  }
  if (parsed.username || parsed.password || (isIssuer && (parsed.search || parsed.hash))) {
    throw new Error(`${label} contains unsupported URL components`);
  }
  return parsed.href.replace(/\/$/, '');
}

export function randomB64Url(bytes: number): string {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return btoa(String.fromCharCode(...arr)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

export async function sha256B64Url(plain: string): Promise<string> {
  const encoded = new TextEncoder().encode(plain);
  const hash = await sha256Bytes(encoded);
  return btoa(String.fromCharCode(...hash)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}
