import { Injectable } from '@angular/core';

import { ChatMessage } from './ai-assistant.types';

@Injectable({ providedIn: 'root' })
export class AiAssistantStorageService {
  persistJson(storageKey: string, value: unknown): void {
    try {
      localStorage.setItem(storageKey, JSON.stringify(value));
    } catch {}
  }

  restoreJson<T>(storageKey: string, fallback: T): T {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw === null) return fallback;
      return JSON.parse(raw) as T;
    } catch {
      return fallback;
    }
  }

  persistBoolean(storageKey: string, value: boolean): void {
    this.persistJson(storageKey, value);
  }

  restoreBoolean(storageKey: string, fallback: boolean): boolean {
    const parsed = this.restoreJson<unknown>(storageKey, fallback);
    return typeof parsed === 'boolean' ? parsed : fallback;
  }

  persistPendingPlan(storageKey: string, msg: ChatMessage): void {
    if (!msg.pendingPrompt || !Array.isArray(msg.toolCalls) || !msg.toolCalls.length) return;
    try {
      localStorage.setItem(
        storageKey,
        JSON.stringify({
          pendingPrompt: msg.pendingPrompt,
          toolCalls: msg.toolCalls,
          createdAt: Date.now(),
        })
      );
    } catch {}
  }

  restorePendingPlan(storageKey: string): { pendingPrompt: string; toolCalls: any[] } | null {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed?.pendingPrompt || !Array.isArray(parsed?.toolCalls) || !parsed.toolCalls.length) return null;
      return {
        pendingPrompt: String(parsed.pendingPrompt),
        toolCalls: parsed.toolCalls,
      };
    } catch {
      return null;
    }
  }

  clear(storageKey: string): void {
    try {
      localStorage.removeItem(storageKey);
    } catch {}
  }
}
