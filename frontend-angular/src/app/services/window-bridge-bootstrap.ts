import { InjectionToken } from '@angular/core';

import type { TuiAuthContext } from './window-bridge.service';
import { allIdentityKeys } from './identity/identity-storage-layout';

export interface WindowBridgeBootstrap {
  readonly bridgeUrl: string;
  readonly windowToken: string;
  readonly auth: TuiAuthContext;
}

export const WINDOW_BRIDGE_BOOTSTRAP = new InjectionToken<WindowBridgeBootstrap | null>(
  'WINDOW_BRIDGE_BOOTSTRAP',
);

const AUTH_STORAGE_KEYS = Object.freeze(allIdentityKeys().map((entry) => entry.key));
const AGENT_DIRECTORY_KEY = 'ananta.agents.v1';

/**
 * Completes the authenticated loopback handoff before Angular services exist.
 *
 * The stale browser auth state is removed synchronously before the first
 * await. Consequently UserAuthService can never send it to an old/default Hub
 * while the one-time TUI context is still being resolved.
 */
export async function prepareWindowBridgeBootstrap(): Promise<WindowBridgeBootstrap | null> {
  const url = new URL(window.location.href);
  const fragment = new URLSearchParams(url.hash.replace(/^#/, ''));
  const rawBridge = url.searchParams.get('bridge');
  const bridgeUrl = normalizeLoopbackBridge(rawBridge);
  const windowToken = fragment.get('ananta_window_token') ?? '';
  const bridgeRequested = rawBridge !== null || fragment.has('ananta_window_token');
  if (!bridgeRequested) return null;

  const bootstrapScrubbed = scrubBootstrapUrl(url);
  if (bridgeUrl && !clearPersistedAuthState()) {
    throw new Error('window_bridge_auth_storage_unavailable');
  }
  if (!bootstrapScrubbed || !bridgeUrl || !/^[a-f0-9]{32}$/.test(windowToken)) {
    return null;
  }

  const abort = new AbortController();
  const timeoutHandle = setTimeout(() => abort.abort(), 5_000);
  try {
    const response = await fetch(`${bridgeUrl}/auth-context`, {
      cache: 'no-store',
      credentials: 'omit',
      headers: { 'X-Ananta-Window-Token': windowToken },
      referrerPolicy: 'no-referrer',
      signal: abort.signal,
    });
    if (!response.ok) return null;
    const auth = parseAuthContext(await response.json() as unknown);
    const normalizedHubUrl = normalizeHubOrigin(auth.hubUrl);
    if ((auth.hubUrl || auth.hubToken) && !normalizedHubUrl) return null;
    const normalizedAuth = { ...auth, hubUrl: normalizedHubUrl ?? '' };
    persistResolvedAuthState(normalizedAuth);
    return { bridgeUrl, windowToken, auth: normalizedAuth };
  } catch {
    return null;
  } finally {
    clearTimeout(timeoutHandle);
  }
}

function clearPersistedAuthState(): boolean {
  let cleared = true;
  for (const key of AUTH_STORAGE_KEYS) {
    try {
      localStorage.removeItem(key);
    } catch {
      cleared = false;
    }
  }
  try {
    const parsed = JSON.parse(localStorage.getItem(AGENT_DIRECTORY_KEY) ?? '[]') as unknown;
    if (Array.isArray(parsed)) {
      const sanitized = parsed.map(item => (
        item && typeof item === 'object'
          ? { ...(item as Record<string, unknown>), token: '' }
          : item
      ));
      localStorage.setItem(AGENT_DIRECTORY_KEY, JSON.stringify(sanitized));
    } else {
      localStorage.removeItem(AGENT_DIRECTORY_KEY);
    }
  } catch {
    try { localStorage.removeItem(AGENT_DIRECTORY_KEY); } catch { cleared = false; }
  }
  return cleared;
}

function persistResolvedAuthState(auth: TuiAuthContext): void {
  try {
    if (auth.hubToken) localStorage.setItem('ananta.user.token', auth.hubToken);
    if (auth.oidcToken) localStorage.setItem('ananta.oidc.access_token', auth.oidcToken);
    if (auth.hubUrl) persistHubOrigin(auth.hubUrl);
  } catch {
    clearPersistedAuthState();
    throw new Error('window_bridge_auth_storage_unavailable');
  }
}

function persistHubOrigin(hubUrl: string): void {
  let agents: Array<Record<string, unknown>> = [];
  try {
    const parsed = JSON.parse(localStorage.getItem(AGENT_DIRECTORY_KEY) ?? '[]') as unknown;
    if (Array.isArray(parsed)) {
      agents = parsed.filter(item => item && typeof item === 'object') as Array<Record<string, unknown>>;
    }
  } catch {
    agents = [];
  }
  const hubIndex = agents.findIndex(item => item['name'] === 'hub' || item['role'] === 'hub');
  const current = hubIndex >= 0 ? agents[hubIndex] : {};
  const hub = { ...current, name: 'hub', role: 'hub', url: hubUrl, token: '' };
  if (hubIndex >= 0) agents[hubIndex] = hub;
  else agents.unshift(hub);
  localStorage.setItem(AGENT_DIRECTORY_KEY, JSON.stringify(agents));
}

function normalizeLoopbackBridge(value: string | null): string {
  if (!value) return '';
  try {
    const url = new URL(value);
    if (
      url.protocol !== 'http:'
      || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || !url.port
      || url.username
      || url.password
      || url.pathname !== '/'
      || url.search
      || url.hash
    ) return '';
    return url.origin;
  } catch {
    return '';
  }
}

function normalizeHubOrigin(value: string): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (
      !['http:', 'https:'].includes(url.protocol)
      || !url.hostname
      || url.username
      || url.password
      || url.pathname !== '/'
      || url.search
      || url.hash
    ) return null;
    return url.origin;
  } catch {
    return null;
  }
}

function scrubBootstrapUrl(url: URL): boolean {
  for (const key of ['bridge', 'token', 'hub_url', 'hub_token', 'oidc_token']) {
    url.searchParams.delete(key);
  }
  url.hash = '';
  try {
    window.history.replaceState(window.history.state, document.title, `${url.pathname}${url.search}`);
    return true;
  } catch {
    return false;
  }
}

function parseAuthContext(payload: unknown): TuiAuthContext {
  if (!payload || typeof payload !== 'object') throw new Error('window_bridge_auth_context_invalid');
  const envelope = payload as Record<string, unknown>;
  if (envelope['ok'] !== true || !envelope['auth'] || typeof envelope['auth'] !== 'object') {
    throw new Error('window_bridge_auth_context_invalid');
  }
  const auth = envelope['auth'] as Record<string, unknown>;
  const text = (key: string, maxLength: number): string => {
    const value = auth[key];
    if (typeof value !== 'string' || value.length > maxLength) {
      throw new Error('window_bridge_auth_context_invalid');
    }
    return value;
  };
  return {
    hubUrl: text('hub_url', 2_048),
    hubToken: text('hub_token', 16_384),
    oidcToken: text('oidc_token', 16_384),
  };
}
