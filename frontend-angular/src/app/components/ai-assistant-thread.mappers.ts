import { ChatMessage, ChatThread } from './ai-assistant.types';

/**
 * Pure helpers for the assistant's local chat threads: storage (de)serialization,
 * default thread creation and title derivation.
 */

export const MAX_THREAD_HISTORY = 40;
const LEGACY_GREETING = 'Hallo. Ich bin AI Snake.';

export function createDefaultThread(history: ChatMessage[] = []): ChatThread {
  return {
    id: 'thread-default',
    title: 'Chat 1',
    history,
    updatedAt: Date.now(),
  };
}

export function createNumberedThread(index: number): ChatThread {
  return {
    id: `thread-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title: `Chat ${index}`,
    history: [],
    updatedAt: Date.now(),
  };
}

/** Compact representation persisted to storage (role/content only). */
export function toStoredThreads(threads: ChatThread[]) {
  return (Array.isArray(threads) ? threads : []).map((thread) => ({
    id: thread.id,
    title: thread.title,
    updatedAt: thread.updatedAt,
    history: thread.history.slice(-MAX_THREAD_HISTORY).map((message) => ({ role: message.role, content: message.content })),
  }));
}

/** Validates stored thread rows; invalid rows and messages are dropped. */
export function parseStoredThreads(stored: unknown): ChatThread[] {
  if (!Array.isArray(stored)) return [];
  return stored
    .map((thread: any) => {
      const rawHistory = Array.isArray(thread?.history) ? thread.history : [];
      const history = rawHistory
        .filter((message: any) => (message?.role === 'user' || message?.role === 'assistant') && typeof message?.content === 'string')
        .filter((message: any) => message.content !== LEGACY_GREETING)
        .map((message: any) => ({ role: message.role, content: message.content } as ChatMessage))
        .slice(-MAX_THREAD_HISTORY);
      const id = String(thread?.id || '').trim();
      if (!id) return null;
      return {
        id,
        title: String(thread?.title || '').trim() || 'Chat',
        history,
        updatedAt: Number(thread?.updatedAt) || Date.now(),
      } as ChatThread;
    })
    .filter((thread): thread is ChatThread => !!thread);
}

/**
 * Returns the title a thread should get after the given prompt, or null when
 * the thread already has a user-visible (non-default) title.
 */
export function deriveThreadTitle(currentTitle: string, prompt: string): string | null {
  const normalized = prompt.replace(/\s+/g, ' ').trim();
  if (!normalized) return null;
  const isDefaultTitle = /^Chat \d+$/.test(currentTitle) || currentTitle === 'Chat';
  if (!isDefaultTitle) return null;
  return normalized.length > 30 ? `${normalized.slice(0, 30)}...` : normalized;
}
