import { TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';

import { AiSnakeChatService, SnakeChatMessage } from './ai-snake-chat.service';
import { ChatHistoryService } from './chat-history.service';
import { ChatSessionsService } from './chat-sessions.service';
import { SnakeGuideService } from './snake-guide.service';

describe('ChatHistoryService regeneration', () => {
  const messages$ = new BehaviorSubject<SnakeChatMessage[]>([]);

  beforeEach(() => {
    localStorage.clear();
    messages$.next([]);
    TestBed.configureTestingModule({
      providers: [
        ChatHistoryService,
        { provide: AiSnakeChatService, useValue: { messages$ } },
        { provide: ChatSessionsService, useValue: { activeSessionId$: new BehaviorSubject('session-1') } },
        { provide: SnakeGuideService, useValue: { acceptGuideForRequest: () => true, play: () => undefined } },
      ],
    });
  });

  it('replaces only the selected AI message and preserves following messages', () => {
    const history = TestBed.inject(ChatHistoryService);
    const initial: SnakeChatMessage[] = [
      { id: 'u1', sender_id: 'user', text: 'question', created_at: 1, channel_type: 'room', session_id: 'session-1' },
      { id: 'a1', sender_id: 'ai-snake', text: 'old answer', created_at: 2, channel_type: 'room', session_id: 'session-1' },
      { id: 'u2', sender_id: 'user', text: 'follow-up', created_at: 3, channel_type: 'room', session_id: 'session-1' },
      { id: 'a2', sender_id: 'ai-snake', text: 'later answer', created_at: 4, channel_type: 'room', session_id: 'session-1' },
    ];
    messages$.next(initial);

    history.replaceWithNextAiMessage('session-1', 'a1', 'regen-prompt');
    messages$.next([
      ...initial,
      { id: 'regen-prompt', sender_id: 'user', text: 'question', created_at: 5, channel_type: 'room', session_id: 'session-1' },
      { id: 'a3', sender_id: 'ai-snake', text: 'new answer', created_at: 6, channel_type: 'room', session_id: 'session-1' },
    ]);

    expect(history.getMessages('session-1').map(message => [message.id, message.text])).toEqual([
      ['u1', 'question'],
      ['a1', 'new answer'],
      ['u2', 'follow-up'],
      ['a2', 'later answer'],
    ]);
  });
});
