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
    if (this.agents.length === 0) {
      // sensible defaults matching docker-compose
      this.agents = [
        { name: 'hub', url: 'http://localhost:5000', token: '', role: 'hub' },
        { name: 'alpha', url: 'http://localhost:5001', token: '', role: 'worker' },
        { name: 'beta', url: 'http://localhost:5002', token: '', role: 'worker' }
      ];
      this.save();
    }
  }

  list(): AgentEntry[] { return [...this.agents]; }
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
}
