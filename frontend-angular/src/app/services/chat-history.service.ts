import { Injectable, inject, OnDestroy } from '@angular/core';
import { BehaviorSubject, Subscription } from 'rxjs';
import { AiSnakeChatService, SnakeChatMessage } from './ai-snake-chat.service';
import { ChatSessionsService } from './chat-sessions.service';
import { SnakeGuideService } from './snake-guide.service';

const GUIDE_MARKER = '\n\n__GUIDE__:';

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
  private guide = inject(SnakeGuideService);

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
    const fallbackSessionId = this.sessions.activeSessionId$.value || 'default';
    let changed = false;
    for (const m of msgs) {
      if (this.knownIds.has(m.id)) continue;
      this.knownIds.add(m.id);

      let text = m.text ?? '';
      const guideIdx = text.indexOf(GUIDE_MARKER);
      if (guideIdx >= 0) {
        // Guide steps are processed regardless of which session the message belongs to
        const guideJson = text.slice(guideIdx + GUIDE_MARKER.length);
        text = text.slice(0, guideIdx);
        try {
          const guide = JSON.parse(guideJson);
          if (Array.isArray(guide?.steps) && guide.steps.length) {
            this.guide.play(guide.steps);
          }
        } catch { /* malformed guide JSON — skip */ }
      }

      // Use session_id from message if present (e.g. ananta-visual), else active session
      const sessionId = m.session_id || fallbackSessionId;

      const entry: ChatHistoryMessage = {
        id: m.id,
        sessionId,
        senderId: m.sender_id,
        text,
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
