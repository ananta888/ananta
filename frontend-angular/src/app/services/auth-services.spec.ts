import { HttpErrorResponse, HttpEventType, HttpRequest } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of, Subject, throwError } from 'rxjs';

import { AgentJwtService } from './agent-jwt.service';
import { AuthRefreshCoordinator } from './auth-refresh-coordinator.service';
import { UserAuthService } from './user-auth.service';

function decodeJwtPayload(token: string): Record<string, unknown> {
  const payload = token.split('.')[1] || '';
  const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
  return JSON.parse(atob(padded));
}

describe('agent auth services', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('creates a short-lived frontend JWT payload from the shared secret', async () => {
    const service = new AgentJwtService();

    const token = await service.createFrontendToken('shared-secret');
    const payload = decodeJwtPayload(token);

    expect(token.split('.')).toHaveLength(3);
    expect(payload['sub']).toBe('frontend');
    expect(typeof payload['iat']).toBe('number');
  });

  it('refreshes a user token once and retries the original request with the new token', async () => {
    const userAuth = {
      token$: of(null),
      refreshToken: vi.fn(() => of({ access_token: 'new-token' })),
      logout: vi.fn(),
      logoutHub: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        AuthRefreshCoordinator,
        { provide: UserAuthService, useValue: userAuth },
      ],
    });
    const coordinator = TestBed.inject(AuthRefreshCoordinator);
    const request = new HttpRequest('GET', '/api/goals');
    const next = {
      handle: vi.fn(() => of({ type: HttpEventType.Sent })),
    };
    const applyToken = vi.fn(
      (req: HttpRequest<unknown>, token: string) => req.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
    );

    await firstValueFrom(coordinator.handleUnauthorized(request, next, applyToken));

    expect(userAuth.refreshToken).toHaveBeenCalledTimes(1);
    expect(applyToken).toHaveBeenCalledWith(request, 'new-token');
    expect(next.handle.mock.calls.at(-1)?.[0].headers.get('Authorization')).toBe('Bearer new-token');
    expect(userAuth.logout).not.toHaveBeenCalled();
  });

  it('shares one in-flight refresh for concurrent 401 handlers', async () => {
    const refresh$ = new Subject<{ access_token: string }>();
    const userAuth = {
      token$: of(null),
      refreshToken: vi.fn(() => refresh$),
      logout: vi.fn(),
      logoutHub: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        AuthRefreshCoordinator,
        { provide: UserAuthService, useValue: userAuth },
      ],
    });
    const coordinator = TestBed.inject(AuthRefreshCoordinator);
    const request = new HttpRequest('GET', '/api/tasks');
    const next = {
      handle: vi.fn(() => of({ type: HttpEventType.Sent })),
    };
    const applyToken = vi.fn(
      (req: HttpRequest<unknown>, token: string) => req.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
    );

    const first = firstValueFrom(coordinator.handleUnauthorized(request, next, applyToken));
    const second = firstValueFrom(coordinator.handleUnauthorized(request, next, applyToken));
    refresh$.next({ access_token: 'shared-token' });
    refresh$.complete();

    await Promise.all([first, second]);

    expect(userAuth.refreshToken).toHaveBeenCalledTimes(1);
    expect(next.handle).toHaveBeenCalledTimes(2);
    expect(applyToken.mock.calls.map(args => args[1])).toEqual(['shared-token', 'shared-token']);
  });

  it('propagates transient refresh failures without ending the Hub session', async () => {
    const refreshError = new Error('refresh failed');
    const userAuth = {
      token$: of(null),
      refreshToken: vi.fn(() => throwError(() => refreshError)),
      logout: vi.fn(),
      logoutHub: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        AuthRefreshCoordinator,
        { provide: UserAuthService, useValue: userAuth },
      ],
    });
    const coordinator = TestBed.inject(AuthRefreshCoordinator);

    await expect(firstValueFrom(
      coordinator.handleUnauthorized(new HttpRequest('GET', '/api/goals'), { handle: vi.fn() }, req => req),
    )).rejects.toBe(refreshError);
    expect(userAuth.logoutHub).not.toHaveBeenCalled();
  });

  it.each([500, 401])('propagates retry status %s without logout or a second refresh', async (status) => {
    const userAuth = {
      token$: of(null),
      refreshToken: vi.fn(() => of({ access_token: 'new-token' })),
      logout: vi.fn(),
      logoutHub: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        AuthRefreshCoordinator,
        { provide: UserAuthService, useValue: userAuth },
      ],
    });
    const coordinator = TestBed.inject(AuthRefreshCoordinator);
    const retryError = new HttpErrorResponse({ status });

    await expect(firstValueFrom(coordinator.handleUnauthorized(
      new HttpRequest('PUT', '/v1/voice/live-runs/run-a/segments/0'),
      { handle: vi.fn(() => throwError(() => retryError)) },
      (request, token) => request.clone({ setHeaders: { Authorization: `Bearer ${token}` } }),
    ))).rejects.toBe(retryError);

    expect(userAuth.refreshToken).toHaveBeenCalledTimes(1);
    expect(userAuth.logoutHub).not.toHaveBeenCalled();
  });
});
