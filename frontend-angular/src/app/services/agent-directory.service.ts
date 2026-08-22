import { Injectable } from '@angular/core';
import { Capacitor } from '@capacitor/core';
import { encrypt, decrypt } from '../utils/crypto';

export interface AgentEntry {
  name: string;
  url: string;
  token?: string;
  role?: 'hub' | 'worker';
}

const LS_KEY = 'ananta.agents.v1';

export function hostBasedAgentOrigin(
  protocol: string,
  hostname: string,
  port: number,
): string {
  const safeProtocol = protocol === 'https:' ? 'https:' : 'http:';
  const normalizedHostname = String(hostname || '').trim() || '127.0.0.1';
  const safeHostname = normalizedHostname.includes(':')
    && !normalizedHostname.startsWith('[')
    ? `[${normalizedHostname}]`
    : normalizedHostname;
  return `${safeProtocol}//${safeHostname}:${port}`;
}

/** Resolve a Hub reached through the standard HTTPS reverse-proxy origin. */
export function browserHubOrigin(
  protocol: string,
  hostname: string,
  browserPort: string,
): string | null {
  if (protocol !== 'https:' || (browserPort && browserPort !== '443')) return null;
  const normalizedHostname = String(hostname || '').trim();
  if (!normalizedHostname) return null;
  const safeHostname = normalizedHostname.includes(':')
    && !normalizedHostname.startsWith('[')
    ? `[${normalizedHostname}]`
    : normalizedHostname;
  return `https://${safeHostname}`;
}

function isPrivateBrowserHostname(hostname: string): boolean {
  const normalized = String(hostname || '').trim().toLowerCase().replace(/^\[|\]$/g, '');
  if (!normalized) return false;
  if (normalized === 'localhost' || normalized.endsWith('.localhost') || normalized.endsWith('.local')) {
    return true;
  }
  if (normalized === '::1' || normalized.startsWith('fc') || normalized.startsWith('fd')) return true;
  if (/^fe[89ab][0-9a-f]:/.test(normalized)) return true;

  const octets = normalized.split('.').map(part => Number(part));
  if (octets.length !== 4 || octets.some(value => !Number.isInteger(value) || value < 0 || value > 255)) {
    return false;
  }
  return octets[0] === 10
    || octets[0] === 127
    || (octets[0] === 169 && octets[1] === 254)
    || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
    || (octets[0] === 192 && octets[1] === 168);
}

/**
 * Replace a stale local Hub boundary when the UI is served by a standard
 * HTTPS edge. Public remote Hub origins remain an explicit user choice.
 */
export function migratedHubOriginForBrowser(
  configuredUrl: string,
  browserProtocol: string,
  browserHostname: string,
  browserPort: string,
): string | null {
  const publicOrigin = browserHubOrigin(browserProtocol, browserHostname, browserPort);
  if (!publicOrigin) return null;
  try {
    const configured = new URL(String(configuredUrl || '').trim());
    if (!['http:', 'https:'].includes(configured.protocol)) return null;
    const sameBrowserHost = configured.hostname.toLowerCase().replace(/^\[|\]$/g, '')
      === String(browserHostname || '').trim().toLowerCase().replace(/^\[|\]$/g, '');
    if (!sameBrowserHost && !isPrivateBrowserHostname(configured.hostname)) return null;
    return configured.origin === publicOrigin ? null : publicOrigin;
  } catch {
    return null;
  }
}

/**
 * A browser behind the standard HTTPS edge talks only to the Hub. Workers are
 * Hub-owned execution targets and must never become direct browser network
 * destinations, especially when stale storage contains LAN addresses.
 */
export function publicEdgeAgents(
  agents: readonly AgentEntry[],
  publicHubOrigin: string | null,
): AgentEntry[] {
  if (!publicHubOrigin) return agents.map(agent => ({ ...agent }));
  const configuredHub = agents.find(agent => agent.role === 'hub' || agent.name === 'hub');
  return [{
    name: configuredHub?.name || 'hub',
    role: 'hub',
    url: publicHubOrigin,
    token: configuredHub?.url === publicHubOrigin ? configuredHub.token : '',
  }];
}

/**
 * Accepts only an HTTP(S) origin suitable as a Hub trust boundary.
 *
 * In particular, login data must never be smuggled into the URL and API
 * requests must not accidentally inherit a path, query or fragment.
 */
export function normalizeHubOrigin(value: string): string | null {
  const candidate = String(value || '').trim();
  if (!candidate || candidate.includes('?') || candidate.includes('#')) return null;

  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    if (!parsed.hostname || parsed.username || parsed.password) return null;
    if (parsed.pathname !== '/' || parsed.search || parsed.hash) return null;

    const authorityStart = candidate.indexOf('://') + 3;
    const authorityEnd = candidate.indexOf('/', authorityStart);
    const authority = candidate.slice(authorityStart, authorityEnd < 0 ? undefined : authorityEnd);
    if (authorityStart < 3 || authority.includes('@')) return null;
    if (authorityEnd >= 0 && candidate.slice(authorityEnd) !== '/') return null;

    return parsed.origin;
  } catch {
    return null;
  }
}

/**
 * Builds the Android control-plane defaults without discarding a Hub that the
 * user explicitly configured. The worker remains device-local because it is
 * managed by the embedded Android runtime.
 */
export function androidRuntimeAgents(agents: readonly AgentEntry[]): AgentEntry[] {
  const currentHub = agents.find((agent) => agent.name === 'hub')
    ?? agents.find((agent) => agent.role === 'hub');
  const currentWorker = agents.find((agent) => agent.name === 'worker')
    ?? agents.find((agent) => agent.role === 'worker');
  const configuredHubUrl = (currentHub?.url || '').trim() || 'http://127.0.0.1:5000';
  return [
    { name: 'hub', role: 'hub', url: configuredHubUrl, token: currentHub?.token ?? '' },
    { name: 'worker', role: 'worker', url: 'http://127.0.0.1:5000', token: currentWorker?.token ?? '' },
  ];
}

/**
 * Rewrites stale loopback entries for the Docker-internal frontend.
 *
 * Credentials already bound to the canonical compose service URL remain
 * intact. A credential is cleared only when its origin is rewritten, because
 * carrying it to a different trust boundary would be unsafe.
 */
export function composeRuntimeAgents(agents: readonly AgentEntry[]): AgentEntry[] {
  const composeUrls: Readonly<Record<string, string>> = Object.freeze({
    hub: 'http://ai-agent-hub:5000',
    alpha: 'http://ai-agent-alpha:5000',
    beta: 'http://ai-agent-beta:5000',
  });
  return agents.map((agent) => {
    const expectedUrl = composeUrls[agent.name || ''];
    if (!expectedUrl) return { ...agent };
    try {
      const hostname = new URL(String(agent.url || '')).hostname.toLowerCase();
      if (hostname !== 'localhost' && hostname !== '127.0.0.1' && hostname !== '::1') {
        return { ...agent };
      }
    } catch {
      return { ...agent };
    }
    return { ...agent, url: expectedUrl, token: '' };
  });
}

/** Whether the configured Android Hub is the embedded loopback control plane. */
export function usesEmbeddedAndroidHub(url: string): boolean {
  try {
    const parsed = new URL(String(url || '').trim());
    const hostname = parsed.hostname.toLowerCase();
    return parsed.protocol === 'http:'
      && ['127.0.0.1', 'localhost', '[::1]'].includes(hostname)
      && parsed.port === '5000'
      && parsed.pathname === '/'
      && !parsed.username
      && !parsed.password
      && !parsed.search
      && !parsed.hash;
  } catch {
    return false;
  }
}

@Injectable({ providedIn: 'root' })
export class AgentDirectoryService {
  private agents: AgentEntry[] = [];

  constructor() {
    this.load();
    this.normalizeLoopbackUrls();
    this.applyRuntimeDefaults();
    this.applyPublicEdgeBoundary();
    if (this.agents.length === 0) {
      this.agents = this.defaultAgentsForCurrentHost();
      this.save();
    }
  }

  list(): AgentEntry[] {
    this.load();
    this.normalizeLoopbackUrls();
    this.applyPublicEdgeBoundary();
    return [...this.agents];
  }
  get(name: string): AgentEntry | undefined { return this.agents.find(a => a.name === name); }
  upsert(entry: AgentEntry) {
    const idx = this.agents.findIndex(a => a.name === entry.name);
    if (idx >= 0) this.agents[idx] = entry; else this.agents.push(entry);
    this.save();
  }
  remove(name: string) {
    this.agents = this.agents.filter(a => a.name !== name);
    this.save();
  }
  private load() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (raw) {
        this.agents = JSON.parse(raw);
        this.agents.forEach(a => {
          if (a.token) a.token = decrypt(a.token);
        });
      }
    } catch {}
  }
  private save() {
    try {
      const toSave = this.agents.map(a => ({
        ...a,
        token: a.token ? encrypt(a.token) : a.token
      }));
      localStorage.setItem(LS_KEY, JSON.stringify(toSave));
    } catch {}
  }

  private currentHostname(): string {
    try {
      return (globalThis?.location?.hostname || '').toLowerCase();
    } catch {
      return '';
    }
  }

  private isComposeInternalFrontendHost(): boolean {
    return this.currentHostname() === 'angular-frontend';
  }

  private isLoopbackHost(host: string): boolean {
    const h = String(host || '').toLowerCase();
    return h === 'localhost' || h === '127.0.0.1' || h === '::1';
  }

  private hostBasedUrl(port: number): string {
    let protocol = 'http:';
    try {
      protocol = globalThis?.location?.protocol || protocol;
    } catch {}
    return hostBasedAgentOrigin(protocol, this.currentHostname(), port);
  }

  private standardHttpsOrigin(): string | null {
    try {
      const location = globalThis?.location;
      return browserHubOrigin(location?.protocol, location?.hostname, location?.port);
    } catch {
      return null;
    }
  }

  private defaultAgentsForCurrentHost(): AgentEntry[] {
    if (Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android') {
      return [
        { name: 'hub', url: 'http://127.0.0.1:5000', token: '', role: 'hub' },
        { name: 'worker', url: 'http://127.0.0.1:5000', token: '', role: 'worker' }
      ];
    }
    if (this.isComposeInternalFrontendHost()) {
      return [
        { name: 'hub', url: 'http://ai-agent-hub:5000', token: '', role: 'hub' },
        { name: 'alpha', url: 'http://ai-agent-alpha:5000', token: '', role: 'worker' },
        { name: 'beta', url: 'http://ai-agent-beta:5000', token: '', role: 'worker' }
      ];
    }
    const host = this.currentHostname();
    const useHostAddress = host && !this.isLoopbackHost(host);
    if (useHostAddress) {
      const publicHubOrigin = this.standardHttpsOrigin();
      return [
        { name: 'hub', url: publicHubOrigin ?? this.hostBasedUrl(5000), token: '', role: 'hub' },
        { name: 'alpha', url: this.hostBasedUrl(5001), token: '', role: 'worker' },
        { name: 'beta', url: this.hostBasedUrl(5002), token: '', role: 'worker' }
      ];
    }
    // sensible defaults for host/browser usage
    return [
      { name: 'hub', url: 'http://127.0.0.1:5000', token: '', role: 'hub' },
      { name: 'alpha', url: 'http://127.0.0.1:5001', token: '', role: 'worker' },
      { name: 'beta', url: 'http://127.0.0.1:5002', token: '', role: 'worker' }
    ];
  }

  private applyRuntimeDefaults() {
    if (Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android') {
      const normalized = androidRuntimeAgents(this.agents);
      const before = JSON.stringify(this.agents);
      const after = JSON.stringify(normalized);
      if (before !== after) {
        this.agents = normalized;
        this.save();
      }
      return;
    }

    if (!this.isComposeInternalFrontendHost() || this.agents.length === 0) return;

    const normalized = composeRuntimeAgents(this.agents);
    if (JSON.stringify(normalized) !== JSON.stringify(this.agents)) {
      this.agents = normalized;
      this.save();
    }
  }

  private applyPublicEdgeBoundary(): void {
    const publicHubOrigin = this.standardHttpsOrigin();
    if (!publicHubOrigin || this.isComposeInternalFrontendHost()) return;
    const normalized = publicEdgeAgents(this.agents, publicHubOrigin);
    if (JSON.stringify(normalized) !== JSON.stringify(this.agents)) {
      this.agents = normalized;
      this.save();
    }
  }

  private normalizeLoopbackUrls() {
    if (this.agents.length === 0) return;
    let changed = false;
    this.agents = this.agents.map((agent) => {
      const raw = String(agent.url || '').trim();
      if (!raw) return agent;
      let normalized = raw;
      try {
        const parsed = new URL(raw);
        if (parsed.hostname.toLowerCase() === 'localhost') {
          parsed.hostname = '127.0.0.1';
          normalized = parsed.toString().replace(/\/$/, '');
        }
      } catch {
        normalized = raw.replace(/^https?:\/\/localhost\b/i, (prefix) =>
          prefix.toLowerCase().startsWith('https://') ? 'https://127.0.0.1' : 'http://127.0.0.1'
        );
      }
      if (normalized !== raw) {
        changed = true;
        return { ...agent, url: normalized };
      }
      return agent;
    });
    if (changed) this.save();

    const host = this.currentHostname();
    if (!host || this.isLoopbackHost(host) || this.isComposeInternalFrontendHost()) return;
    let rewritten = false;
    this.agents = this.agents.map((a) => {
      const raw = String(a.url || '').trim();
      if (!raw) return a;
      try {
        const parsed = new URL(raw);
        const migratedHubOrigin = a.role === 'hub' || a.name === 'hub'
          ? migratedHubOriginForBrowser(
              raw,
              globalThis.location.protocol,
              host,
              globalThis.location.port,
            )
          : null;
        if (migratedHubOrigin) {
          rewritten = true;
          return { ...a, url: migratedHubOrigin, token: '' };
        }
        if (!this.isLoopbackHost(parsed.hostname)) return a;
        const publicHubOrigin = a.role === 'hub' || a.name === 'hub'
          ? this.standardHttpsOrigin()
          : null;
        const preferredPort = parsed.port || (parsed.protocol === 'https:' ? '443' : '80');
        const next = publicHubOrigin ?? `${globalThis.location.protocol}//${host}:${preferredPort}`;
        rewritten = true;
        return { ...a, url: next, token: '' };
      } catch {
        return a;
      }
    });
    if (rewritten) this.save();
  }
}
