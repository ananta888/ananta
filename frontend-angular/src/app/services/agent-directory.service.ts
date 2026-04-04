import { Injectable } from '@angular/core';
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
    this.applyRuntimeDefaults();
    if (this.agents.length === 0) {
      this.agents = this.defaultAgentsForCurrentHost();
      this.save();
    }
  }

  list(): AgentEntry[] {
    this.load();
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

  private defaultAgentsForCurrentHost(): AgentEntry[] {
    if (this.isComposeInternalFrontendHost()) {
      return [
        { name: 'hub', url: 'http://ai-agent-hub:5000', token: '', role: 'hub' },
        { name: 'alpha', url: 'http://ai-agent-alpha:5000', token: '', role: 'worker' },
        { name: 'beta', url: 'http://ai-agent-beta:5000', token: '', role: 'worker' }
      ];
    }
    // sensible defaults for host/browser usage
    return [
      { name: 'hub', url: 'http://localhost:5000', token: '', role: 'hub' },
      { name: 'alpha', url: 'http://localhost:5001', token: '', role: 'worker' },
      { name: 'beta', url: 'http://localhost:5002', token: '', role: 'worker' }
    ];
  }

  private applyRuntimeDefaults() {
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
      if (current.includes('localhost') || current.includes('127.0.0.1')) {
        changed = true;
        return { ...a, url: expectedUrl };
      }
      return a;
    });

    if (changed) this.save();
  }
}
