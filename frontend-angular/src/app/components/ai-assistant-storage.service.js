var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable } from '@angular/core';
let AiAssistantStorageService = class AiAssistantStorageService {
    persistBoolean(storageKey, value) {
        try {
            localStorage.setItem(storageKey, JSON.stringify(value));
        }
        catch { }
    }
    restoreBoolean(storageKey, fallback) {
        try {
            const raw = localStorage.getItem(storageKey);
            if (raw === null)
                return fallback;
            const parsed = JSON.parse(raw);
            return typeof parsed === 'boolean' ? parsed : fallback;
        }
        catch {
            return fallback;
        }
    }
    persistPendingPlan(storageKey, msg) {
        if (!msg.pendingPrompt || !Array.isArray(msg.toolCalls) || !msg.toolCalls.length)
            return;
        try {
            localStorage.setItem(storageKey, JSON.stringify({
                pendingPrompt: msg.pendingPrompt,
                toolCalls: msg.toolCalls,
                createdAt: Date.now(),
            }));
        }
        catch { }
    }
    restorePendingPlan(storageKey) {
        try {
            const raw = localStorage.getItem(storageKey);
            if (!raw)
                return null;
            const parsed = JSON.parse(raw);
            if (!parsed?.pendingPrompt || !Array.isArray(parsed?.toolCalls) || !parsed.toolCalls.length)
                return null;
            return {
                pendingPrompt: String(parsed.pendingPrompt),
                toolCalls: parsed.toolCalls,
            };
        }
        catch {
            return null;
        }
    }
    clear(storageKey) {
        try {
            localStorage.removeItem(storageKey);
        }
        catch { }
    }
};
AiAssistantStorageService = __decorate([
    Injectable({ providedIn: 'root' })
], AiAssistantStorageService);
export { AiAssistantStorageService };
//# sourceMappingURL=ai-assistant-storage.service.js.map