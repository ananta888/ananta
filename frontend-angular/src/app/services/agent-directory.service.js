var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable } from '@angular/core';
import { encrypt, decrypt } from '../utils/crypto';
const LS_KEY = 'ananta.agents.v1';
let AgentDirectoryService = class AgentDirectoryService {
    constructor() {
        this.agents = [];
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
    list() {
        this.load();
        return [...this.agents];
    }
    get(name) { return this.agents.find(a => a.name === name); }
    upsert(entry) {
        const idx = this.agents.findIndex(a => a.name === entry.name);
        if (idx >= 0)
            this.agents[idx] = entry;
        else
            this.agents.push(entry);
        this.save();
    }
    remove(name) {
        this.agents = this.agents.filter(a => a.name !== name);
        this.save();
    }
    load() {
        try {
            const raw = localStorage.getItem(LS_KEY);
            if (raw) {
                this.agents = JSON.parse(raw);
                this.agents.forEach(a => {
                    if (a.token)
                        a.token = decrypt(a.token);
                });
            }
        }
        catch { }
    }
    save() {
        try {
            const toSave = this.agents.map(a => ({
                ...a,
                token: a.token ? encrypt(a.token) : a.token
            }));
            localStorage.setItem(LS_KEY, JSON.stringify(toSave));
        }
        catch { }
    }
};
AgentDirectoryService = __decorate([
    Injectable({ providedIn: 'root' })
], AgentDirectoryService);
export { AgentDirectoryService };
//# sourceMappingURL=agent-directory.service.js.map