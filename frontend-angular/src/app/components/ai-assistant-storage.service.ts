import { Injectable } from '@angular/core';

import { ChatMessage } from './ai-assistant.types';

@Injectable({ providedIn: 'root' })
export class AiAssistantStorageService {
  persistBoolean(storageKey: string, value: boolean): void {
    try {
      localStorage.setItem(storageKey, JSON.stringify(value));
    } catch {}
  }

  restoreBoolean(storageKey: string, fallback: boolean): boolean {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw === null) return fallback;
      const parsed = JSON.parse(raw);
      return typeof parsed === 'boolean' ? parsed : fallback;
    } catch {
      return fallback;
    }
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
