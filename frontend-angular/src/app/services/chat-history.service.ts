import { Injectable, inject, OnDestroy } from '@angular/core';
import { BehaviorSubject, Subscription } from 'rxjs';
import { AiSnakeChatService, SnakeChatMessage } from './ai-snake-chat.service';
import { ChatSessionsService } from './chat-sessions.service';

export interface ChatHistoryMessage {
  id: string;
  sessionId: string;
  senderId: string;
  text: string;
  ts: number;
  isAI: boolean;
}

const LS_KEY = 'ananta.chat.history.v2';
const MAX_PER_SESSION = 200;

@Injectable({ providedIn: 'root' })
export class ChatHistoryService implements OnDestroy {
  private snake = inject(AiSnakeChatService);
  private sessions = inject(ChatSessionsService);

  private store: Record<string, ChatHistoryMessage[]> = {};
  private knownIds = new Set<string>();
  private sub?: Subscription;

  readonly updated$ = new BehaviorSubject<number>(0);

  constructor() {
    this.load();
    this.sub = this.snake.messages$.subscribe(msgs => this.onMessages(msgs));
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  getMessages(sessionId: string): ChatHistoryMessage[] {
    return this.store[sessionId] ?? [];
  }

  allSessionIds(): string[] {
    return Object.keys(this.store).filter(id => this.store[id].length > 0);
  }

  clearSession(sessionId: string): void {
    this.store[sessionId] = [];
    this.persist();
    this.updated$.next(Date.now());
  }

  clearAll(): void {
    this.store = {};
    this.knownIds.clear();
    this.persist();
    this.updated$.next(Date.now());
  }

  private onMessages(msgs: SnakeChatMessage[]): void {
    const sessionId = this.sessions.activeSessionId$.value || 'default';
    let changed = false;
    for (const m of msgs) {
      if (this.knownIds.has(m.id)) continue;
      this.knownIds.add(m.id);
      const entry: ChatHistoryMessage = {
        id: m.id,
        sessionId,
        senderId: m.sender_id,
        text: m.text,
        ts: m.created_at || Date.now(),
        isAI: m.sender_id?.startsWith('ai') || m.sender_id?.startsWith('tutor') || m.sender_id?.includes('snake'),
      };
      if (!this.store[sessionId]) this.store[sessionId] = [];
      this.store[sessionId].push(entry);
      if (this.store[sessionId].length > MAX_PER_SESSION) {
        this.store[sessionId] = this.store[sessionId].slice(-MAX_PER_SESSION);
      }
      changed = true;
    }
    if (changed) {
      this.persist();
      this.updated$.next(Date.now());
    }
  }

  private load(): void {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        this.store = parsed;
        for (const msgs of Object.values(this.store)) {
          for (const m of (msgs as ChatHistoryMessage[])) {
            this.knownIds.add(m.id);
          }
        }
      }
    } catch {}
  }

  private persist(): void {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(this.store));
    } catch {}
  }
}
