import { TestBed } from '@angular/core/testing';
import { SnakeEventsService, type Candidate } from './snake-events.service';
import { AiSnakeChatService } from './ai-snake-chat.service';
import { AgentDirectoryService } from './agent-directory.service';

describe('SnakeEventsService', () => {
  let service: SnakeEventsService;
  let chat: {
    active$: ReturnType<typeof vi.fn>;
    snakeId$: ReturnType<typeof vi.fn>;
    getSnakeToken: ReturnType<typeof vi.fn>;
  };
  let directory: { list: ReturnType<typeof vi.fn> };
  let eventSourceMocks: Array<{
    onopen: ((ev?: Event) => void) | null;
    onmessage: ((ev: MessageEvent<string>) => void) | null;
    onerror: ((ev?: Event) => void) | null;
    close: ReturnType<typeof vi.fn>;
  }>;

  beforeEach(() => {
    eventSourceMocks = [];
    const ActiveSubject = require('rxjs').BehaviorSubject;

    chat = {
      active$: new ActiveSubject<boolean>(false),
      snakeId$: new ActiveSubject<string>(''),
      getSnakeToken: vi.fn(() => 'token-123'),
    };
    directory = {
      list: vi.fn(() => [{ name: 'hub', role: 'hub', url: 'http://hub:5000' }]),
    };

    globalThis.EventSource = vi.fn((url: string) => {
      const mock = {
        url,
        onopen: null as ((ev?: Event) => void) | null,
        onmessage: null as ((ev: MessageEvent<string>) => void) | null,
        onerror: null as ((ev?: Event) => void) | null,
        close: vi.fn(),
      };
      eventSourceMocks.push(mock);
      return mock;
    }) as unknown as typeof EventSource;

    TestBed.configureTestingModule({
      providers: [
        SnakeEventsService,
        { provide: AiSnakeChatService, useValue: chat },
        { provide: AgentDirectoryService, useValue: directory },
      ],
    });
    service = TestBed.inject(SnakeEventsService);
  });

  afterEach(() => {
    service.disconnect();
  });

  it('opens EventSource when snake becomes active', () => {
    chat.snakeId$.next('snake-1');
    chat.active$.next(true);

    expect(globalThis.EventSource).toHaveBeenCalledTimes(1);
    expect((globalThis.EventSource as any).mock.calls[0][0]).toContain('/snakes/snake-1/events/stream');
    expect((globalThis.EventSource as any).mock.calls[0][0]).toContain('token=token-123');
  });

  it('emits guide events to guide$', () => {
    const guideSpy = vi.fn();
    service.guide$.subscribe(guideSpy);

    chat.snakeId$.next('snake-1');
    chat.active$.next(true);
    const es = eventSourceMocks[0];
    es.onopen?.({} as Event);
    es.onmessage?.(new MessageEvent('message', {
      data: JSON.stringify({ type: 'guide', ts: 1, payload: { request_id: 'r1', trigger_type: 'ui_tick', steps: [{ waypoint: 'a', bubble: 'A' }] } }),
    }));

    expect(guideSpy).toHaveBeenCalledWith(expect.objectContaining({ request_id: 'r1', steps: [{ waypoint: 'a', bubble: 'A' }] }));
  });

  it('emits candidate events to candidates$', () => {
    const candidateSpy = vi.fn();
    service.candidates$.subscribe(candidateSpy);

    chat.snakeId$.next('snake-1');
    chat.active$.next(true);
    const es = eventSourceMocks[0];
    const candidates: Candidate[] = [{ label: 'primary', bubble: 'Hauptvorschlag', steps: [] }];
    es.onmessage?.(new MessageEvent('message', {
      data: JSON.stringify({ type: 'candidates', ts: 1, payload: { request_id: 'r2', candidates } }),
    }));

    expect(candidateSpy).toHaveBeenCalledWith(expect.objectContaining({ request_id: 'r2', candidates }));
  });

  it('reconnects with exponential backoff on error', async () => {
    vi.useFakeTimers();
    try {
      chat.snakeId$.next('snake-1');
      chat.active$.next(true);

      expect(eventSourceMocks).toHaveLength(1);
      eventSourceMocks[0].onerror?.({} as Event);

      await vi.advanceTimersByTimeAsync(1000);
      expect(eventSourceMocks).toHaveLength(2);

      eventSourceMocks[1].onerror?.({} as Event);
      await vi.advanceTimersByTimeAsync(2000);
      expect(eventSourceMocks).toHaveLength(3);

      service.disconnect();
    } finally {
      vi.useRealTimers();
    }
  });

  it('closes EventSource on disconnect', () => {
    chat.snakeId$.next('snake-1');
    chat.active$.next(true);
    const es = eventSourceMocks[0];
    service.disconnect();
    expect(es.close).toHaveBeenCalled();
  });
});
