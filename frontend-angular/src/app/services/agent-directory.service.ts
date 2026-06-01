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

@Injectable({ providedIn: 'root' })
export class AgentDirectoryService {
  private agents: AgentEntry[] = [];

  constructor() {
    this.load();
    this.normalizeLoopbackUrls();
    this.applyRuntimeDefaults();
    if (this.agents.length === 0) {
      this.agents = this.defaultAgentsForCurrentHost();
      this.save();
    }
  }

  list(): AgentEntry[] {
    this.load();
    this.normalizeLoopbackUrls();
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
    const host = this.currentHostname();
    const safeHost = host || '127.0.0.1';
    return `http://${safeHost}:${port}`;
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
      return [
        { name: 'hub', url: this.hostBasedUrl(5000), token: '', role: 'hub' },
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
      const currentHub = this.agents.find((a) => a.name === 'hub') ?? this.agents.find((a) => a.role === 'hub');
      const currentWorker = this.agents.find((a) => a.name === 'worker') ?? this.agents.find((a) => a.role === 'worker');
      const normalized: AgentEntry[] = [
        { name: 'hub', role: 'hub', url: 'http://127.0.0.1:5000', token: currentHub?.token ?? '' },
        { name: 'worker', role: 'worker', url: 'http://127.0.0.1:5000', token: currentWorker?.token ?? '' },
      ];
      const before = JSON.stringify(this.agents);
      const after = JSON.stringify(normalized);
      if (before !== after) {
        this.agents = normalized;
        this.save();
      }
      return;
    }

    if (!this.isComposeInternalFrontendHost() || this.agents.length === 0) return;

    let changed = false;
    const byName: Record<string, string> = {
      hub: 'http://ai-agent-hub:5000',
      alpha: 'http://ai-agent-alpha:5000',
      beta: 'http://ai-agent-beta:5000',
    };

    this.agents = this.agents.map((a) => {
      const expectedUrl = byName[a.name || ''];
      if (!expectedUrl) return a;
      const current = (a.url || '').toLowerCase();
      const mustResetToken = Boolean(a.token);
      if (current.includes('localhost') || current.includes('127.0.0.1') || mustResetToken) {
        changed = true;
        return { ...a, url: expectedUrl, token: '' };
      }
      return a;
    });

    if (changed) this.save();
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
        if (!this.isLoopbackHost(parsed.hostname)) return a;
        const preferredPort = parsed.port || (parsed.protocol === 'https:' ? '443' : '80');
        const next = `http://${host}:${preferredPort}`;
        rewritten = true;
        return { ...a, url: next, token: '' };
      } catch {
        return a;
      }
    });
    if (rewritten) this.save();
  }
}
