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
  hasGuide?: boolean;
  kind?: 'summary';           // set for AI-generated partial summaries
  summarizedIds?: string[];   // ids of the messages this summary replaces
  hiddenBySummary?: string;   // id of the summary message that folded this one away
  includedInContext?: boolean; // false excludes the message from future AI turns
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

  /** Insert a summary message replacing the given messages. The originals are
   *  kept but marked hidden; the summary is inserted at the position of the
   *  first replaced message. Returns the new summary message id. */
  insertSummary(sessionId: string, summaryText: string, replacedIds: string[]): string {
    const msgs = this.store[sessionId] ?? [];
    const firstIdx = msgs.findIndex(m => replacedIds.includes(m.id));
    if (firstIdx < 0) return '';
    const summaryId = 'summary-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
    for (const m of msgs) {
      if (replacedIds.includes(m.id)) m.hiddenBySummary = summaryId;
    }
    const entry: ChatHistoryMessage = {
      id: summaryId, sessionId, senderId: 'summary',
      text: summaryText, ts: Date.now(), isAI: true,
      kind: 'summary', summarizedIds: [...replacedIds],
    };
    msgs.splice(firstIdx, 0, entry);
    this.knownIds.add(summaryId);
    this.persist();
    this.updated$.next(Date.now());
    return summaryId;
  }

  /** Remove a summary and unhide its folded messages. */
  dissolveSummary(sessionId: string, summaryId: string): void {
    const msgs = this.store[sessionId] ?? [];
    const idx = msgs.findIndex(m => m.id === summaryId && m.kind === 'summary');
    if (idx < 0) return;
    for (const m of msgs) {
      if (m.hiddenBySummary === summaryId) delete m.hiddenBySummary;
    }
    msgs.splice(idx, 1);
    this.knownIds.delete(summaryId);
    this.persist();
    this.updated$.next(Date.now());
  }

  /** Messages with summary-folded ones filtered out (for prompt building / display). */
  getVisibleMessages(sessionId: string): ChatHistoryMessage[] {
    return (this.store[sessionId] ?? []).filter(m => !m.hiddenBySummary);
  }

  /** Visible messages explicitly enabled for the next AI turn. */
  getContextMessages(sessionId: string): ChatHistoryMessage[] {
    return this.getVisibleMessages(sessionId).filter(m => m.includedInContext !== false);
  }

  updateMessage(sessionId: string, messageId: string, text: string): boolean {
    const message = (this.store[sessionId] ?? []).find(m => m.id === messageId);
    const normalized = String(text || '').trim();
    if (!message || !normalized) return false;
    message.text = normalized;
    this.commitUpdate();
    return true;
  }

  setIncludedInContext(sessionId: string, messageId: string, included: boolean): void {
    const message = (this.store[sessionId] ?? []).find(m => m.id === messageId);
    if (!message) return;
    message.includedInContext = included;
    this.commitUpdate();
  }

  deleteMessage(sessionId: string, messageId: string): void {
    const messages = this.store[sessionId] ?? [];
    const message = messages.find(m => m.id === messageId);
    if (!message) return;
    if (message.kind === 'summary') {
      for (const original of messages) {
        if (original.hiddenBySummary === messageId) delete original.hiddenBySummary;
      }
    }
    for (const summary of messages) {
      if (summary.kind === 'summary' && summary.summarizedIds?.includes(messageId)) {
        summary.summarizedIds = summary.summarizedIds.filter(id => id !== messageId);
      }
    }
    this.store[sessionId] = messages.filter(m => m.id !== messageId);
    this.commitUpdate();
  }

  /** Originals folded under a summary (for expand view). */
  getSummarizedMessages(sessionId: string, summaryId: string): ChatHistoryMessage[] {
    return (this.store[sessionId] ?? []).filter(m => m.hiddenBySummary === summaryId);
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
      let hasGuide = false;
      const guideIdx = text.indexOf(GUIDE_MARKER);
      if (guideIdx >= 0) {
        // Guide steps are processed regardless of which session the message belongs to
        const guideJson = text.slice(guideIdx + GUIDE_MARKER.length);
        text = text.slice(0, guideIdx);
        try {
          const guide = JSON.parse(guideJson);
          if (Array.isArray(guide?.steps) && guide.steps.length) {
            hasGuide = true;
            const requestId: string | undefined = guide.request_id;
            // Skip stale responses when the pending request_id no longer matches.
            if (!requestId || this.guide.acceptGuideForRequest(requestId)) {
              this.guide.play(guide.steps, { requestId, priority: 2 });
            }
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
        hasGuide,
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

  private commitUpdate(): void {
    this.persist();
    this.updated$.next(Date.now());
  }
}
