import { TestBed } from '@angular/core/testing';
import { HTTP_INTERCEPTORS, provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { NEVER, defer, firstValueFrom, of, throwError } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { AuthInterceptor } from '../../services/auth.interceptor';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetDialogApiService } from './meet-dialog-api.service';
import { dialogStartRequest } from './meet-dialog-start-request';
import { PendingDialogStart } from './meet-dialog-start-attempt';

const body = () => ({ capabilities: ['screen.publish'], duration_seconds: 300, chat_mode: 'off' });
const receipt = { schema: 'ananta.meet-dialog-start.v1', task_id: 'task', session_id: 'session', status: 'connecting' };

describe('immutable keyed Meet start operation', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it('takes an independent deeply immutable JSON snapshot and a fresh key per command', () => {
    const source = body(), first = dialogStartRequest(source), second = dialogStartRequest(source);
    source.capabilities.push('audio.receive'); source.duration_seconds = 600;
    expect(first.body).toEqual(body()); expect(second.body).toEqual(body());
    expect(first.headers['Idempotency-Key']).not.toEqual(second.headers['Idempotency-Key']);
    expect(Object.isFrozen(first.body)).toBe(true);
    expect(Object.isFrozen((first.body as ReturnType<typeof body>).capabilities)).toBe(true);
    expect(Object.isFrozen(first.headers)).toBe(true);
  });

  it.each([undefined, { private: 'x'.repeat(2049) }, { private: 'ä'.repeat(1100) }, { private: 1n }])(
    'rejects unrepresentable or oversized JSON with a fixed diagnostic', value => {
      expect(() => dialogStartRequest(value)).toThrow('meet_dialog_start_request_invalid');
    },
  );

  it('never substitutes an unkeyed start when secure randomness fails', async () => {
    vi.spyOn(crypto, 'randomUUID').mockImplementation(() => { throw new Error('SYNTHETIC_PRIVATE_RANDOM_ERROR'); });
    const core = { request: vi.fn() };
    TestBed.configureTestingModule({ providers: [{ provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } }] });
    await expect(firstValueFrom(TestBed.inject(MeetDialogApiService).start('p', '', body())))
      .rejects.toThrow('meet_dialog_start_request_invalid');
    expect(core.request).not.toHaveBeenCalled();
  });

  it('resubscribes to one fixed URL/body/key without constructing or retrying another command', async () => {
    let subscriptions = 0, hub = 'https://hub.test';
    const core = { request: vi.fn(() => defer(() => ++subscriptions === 1
      ? throwError(() => ({ status: 503 })) : of(receipt))) };
    TestBed.configureTestingModule({ providers: [{ provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: hub }] } }] });
    const api = TestBed.inject(MeetDialogApiService), source = body();
    const operation = api.start('project / one', 'parent / one', source);
    source.capabilities.push('audio.receive'); hub = 'https://other-hub.test';
    await expect(firstValueFrom(operation)).rejects.toEqual({ status: 503 });
    expect(subscriptions).toBe(1);
    await expect(firstValueFrom(operation)).resolves.toEqual(receipt);
    expect(subscriptions).toBe(2);
    expect(core.request).toHaveBeenCalledExactlyOnceWith('POST',
      'https://hub.test/api/meet/v1/projects/project%20%2F%20one/tasks/parent%20%2F%20one/dialogs',
      'https://hub.test', { body: body(), headers: { 'Idempotency-Key': expect.any(String) } });
  });

  it('rejects a malformed receipt without interpreting it as a completed start', async () => {
    const core = { request: vi.fn(() => of({ ...receipt, grant: 'SYNTHETIC_FORBIDDEN' })) };
    TestBed.configureTestingModule({ providers: [{ provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } }] });
    await expect(firstValueFrom(TestBed.inject(MeetDialogApiService).start('p', '', body())))
      .rejects.toThrow('meet_dialog_contract_invalid');
    expect(core.request).toHaveBeenCalledOnce();
  });

  it('preserves the key and body through actual HttpClient/Core/401-refresh handling', async () => {
    const user = { token: 'synthetic-old', token$: of('synthetic-old'),
      refreshToken: vi.fn(() => of({ access_token: 'synthetic-new' })), logout: vi.fn(), logoutHub: vi.fn() };
    TestBed.configureTestingModule({ providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting(),
      { provide: HTTP_INTERCEPTORS, useClass: AuthInterceptor, multi: true },
      { provide: UserAuthService, useValue: user },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } }] });
    const controller = TestBed.inject(HttpTestingController);
    const result = firstValueFrom(TestBed.inject(MeetDialogApiService).start('p', '', body()));
    const path = 'https://hub.test/api/meet/v1/projects/p/dialogs';
    const original = controller.expectOne(path), key = original.request.headers.get('Idempotency-Key');
    expect(original.request.headers.get('Authorization')).toBe('Bearer synthetic-old');
    expect(key).toMatch(/^[a-f0-9-]{36}$/);
    original.flush({}, { status: 401, statusText: 'Synthetic token expired' });
    const refreshed = controller.expectOne(path);
    expect(refreshed.request.headers.get('Idempotency-Key')).toBe(key);
    expect(refreshed.request.headers.get('Authorization')).toBe('Bearer synthetic-new');
    expect(refreshed.request.body).toEqual(body());
    refreshed.flush(receipt, { status: 202, statusText: 'Accepted' });
    await expect(result).resolves.toEqual(receipt);
    expect(user.refreshToken).toHaveBeenCalledOnce(); controller.verify();
  });

  it('bounds an unanswered operation at ten seconds without resubscribing', async () => {
    vi.useFakeTimers();
    let subscriptions = 0;
    const core = { request: vi.fn(() => defer(() => { subscriptions++; return NEVER; })) };
    TestBed.configureTestingModule({ providers: [{ provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } }] });
    const result = firstValueFrom(TestBed.inject(MeetDialogApiService).start('p', '', body()));
    const rejected = expect(result).rejects.toMatchObject({ name: 'TimeoutError' });
    await vi.advanceTimersByTimeAsync(10_000); await rejected;
    expect(subscriptions).toBe(1); expect(core.request).toHaveBeenCalledOnce();
  });

  it('keeps only one unresolved operation without subscribing or persisting it', () => {
    let subscriptions = 0;
    const operation = defer(() => { subscriptions++; return of(receipt); });
    const attempt = new PendingDialogStart();
    expect(attempt.current()).toBeUndefined(); expect(attempt.pending()).toBe(false);
    attempt.begin(operation); expect(attempt.current()).toBe(operation); expect(attempt.pending()).toBe(true);
    expect(() => attempt.begin(of(receipt))).toThrow('meet_dialog_start_already_pending');
    expect(subscriptions).toBe(0);
    attempt.clear(); expect(attempt.pending()).toBe(false); expect(attempt.current()).toBeUndefined();
    attempt.begin(of(receipt)); expect(attempt.pending()).toBe(true);
  });
});
