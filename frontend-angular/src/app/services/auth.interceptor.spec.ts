import { HttpErrorResponse, HttpHandler, HttpRequest, HttpResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { Subject, firstValueFrom, of, throwError } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { AuthInterceptor } from './auth.interceptor';
import { UserAuthService } from './user-auth.service';
import { AuthRefreshCoordinator } from './auth-refresh-coordinator.service';

describe('AuthInterceptor', () => {
  let directory: { list: ReturnType<typeof vi.fn> };
  let userAuth: {
    token: string | null;
    token$: ReturnType<typeof of>;
    refreshToken: ReturnType<typeof vi.fn>;
    logout: ReturnType<typeof vi.fn>;
    logoutHub: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    directory = {
      list: vi.fn(() => [
        { name: 'hub', role: 'hub', url: 'http://hub:5000', token: 'hub-secret' },
        { name: 'worker', role: 'worker', url: 'http://worker:5001', token: 'worker-secret' },
      ]),
    };
    userAuth = {
      token: 'old-user-token',
      token$: of('old-user-token'),
      refreshToken: vi.fn(() => of({ access_token: 'new-user-token' })),
      logout: vi.fn(),
      logoutHub: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        AuthInterceptor,
        { provide: AgentDirectoryService, useValue: directory },
        { provide: UserAuthService, useValue: userAuth },
      ],
    });
  });

  function interceptor(): AuthInterceptor {
    return TestBed.inject(AuthInterceptor);
  }

  it('refreshes and retries hub user-token requests after 401', async () => {
    const seenAuthHeaders: Array<string | null> = [];
    let calls = 0;
    const handler: HttpHandler = {
      handle: vi.fn((request: HttpRequest<unknown>) => {
        calls += 1;
        seenAuthHeaders.push(request.headers.get('Authorization'));
        if (calls === 1) {
          return throwError(() => new HttpErrorResponse({ status: 401, url: request.url }));
        }
        return of(new HttpResponse({ status: 200 }));
      }),
    };

    await firstValueFrom(interceptor().intercept(new HttpRequest('GET', 'http://hub:5000/tasks'), handler));

    expect(userAuth.refreshToken).toHaveBeenCalledTimes(1);
    expect(userAuth.logout).not.toHaveBeenCalled();
    expect(seenAuthHeaders).toEqual(['Bearer old-user-token', 'Bearer new-user-token']);
  });

  it('preserves explicit current-user and foreign bearer ownership without refresh', async () => {
    const userHandler: HttpHandler = {
      handle: vi.fn(() => throwError(() => new HttpErrorResponse({ status: 401 }))),
    };
    const userRequest = new HttpRequest('GET', 'http://hub:5000/tasks').clone({
      setHeaders: { Authorization: 'Bearer old-user-token' },
    });

    await expect(firstValueFrom(interceptor().intercept(userRequest, userHandler)))
      .rejects.toBeTruthy();
    const foreignHandler: HttpHandler = {
      handle: vi.fn(() => throwError(() => new HttpErrorResponse({ status: 401 }))),
    };
    const foreignRequest = new HttpRequest('GET', 'http://hub:5000/tasks').clone({
      setHeaders: { Authorization: 'Bearer service-owned-token' },
    });
    await expect(firstValueFrom(interceptor().intercept(foreignRequest, foreignHandler)))
      .rejects.toBeTruthy();
    expect(userAuth.refreshToken).not.toHaveBeenCalled();
  });

  it('never recursively refreshes either Hub refresh-token endpoint', async () => {
    const handler: HttpHandler = {
      handle: vi.fn(() => throwError(() => new HttpErrorResponse({ status: 401 }))),
    };

    await expect(firstValueFrom(interceptor().intercept(
      new HttpRequest('POST', 'http://hub:5000/refresh-token'), handler,
    ))).rejects.toBeTruthy();
    await expect(firstValueFrom(interceptor().intercept(
      new HttpRequest('POST', 'http://hub:5000/auth/refresh-token'), handler,
    ))).rejects.toBeTruthy();

    expect(handler.handle).toHaveBeenCalledTimes(2);
    expect(userAuth.refreshToken).not.toHaveBeenCalled();
    expect(userAuth.logoutHub).not.toHaveBeenCalled();
  });

  it('does not run user refresh for shared-secret agent JWT requests', async () => {
    const seenAuthHeaders: Array<string | null> = [];
    const handler: HttpHandler = {
      handle: vi.fn((request: HttpRequest<unknown>) => {
        seenAuthHeaders.push(request.headers.get('Authorization'));
        return throwError(() => new HttpErrorResponse({ status: 401, url: request.url }));
      }),
    };

    await expect(
      firstValueFrom(interceptor().intercept(new HttpRequest('GET', 'http://worker:5001/tasks'), handler)),
    ).rejects.toBeTruthy();

    expect(userAuth.refreshToken).not.toHaveBeenCalled();
    expect(userAuth.logout).not.toHaveBeenCalled();
    expect(seenAuthHeaders[0]).toMatch(/^Bearer /);
    expect(seenAuthHeaders[0]).not.toBe('Bearer old-user-token');
  });

  it('logs out when hub token refresh fails', async () => {
    userAuth.refreshToken.mockReturnValueOnce(throwError(() => (
      new HttpErrorResponse({ status: 401, statusText: 'Invalid refresh token' })
    )));
    const handler: HttpHandler = {
      handle: vi.fn((request: HttpRequest<unknown>) => (
        throwError(() => new HttpErrorResponse({ status: 401, url: request.url }))
      )),
    };

    await expect(
      firstValueFrom(interceptor().intercept(new HttpRequest('GET', 'http://hub:5000/tasks'), handler)),
    ).rejects.toBeTruthy();

    expect(userAuth.refreshToken).toHaveBeenCalledTimes(1);
    expect(userAuth.logoutHub).toHaveBeenCalledTimes(1);
  });

  it('logs out exactly once when one invalid refresh rejects concurrent Hub requests', async () => {
    const refresh = new Subject<{ access_token: string }>();
    userAuth.refreshToken.mockReturnValue(refresh);
    const handler: HttpHandler = {
      handle: vi.fn((request: HttpRequest<unknown>) => (
        throwError(() => new HttpErrorResponse({ status: 401, url: request.url }))
      )),
    };

    const first = firstValueFrom(interceptor().intercept(
      new HttpRequest('GET', 'http://hub:5000/tasks/one'), handler,
    ));
    const second = firstValueFrom(interceptor().intercept(
      new HttpRequest('GET', 'http://hub:5000/tasks/two'), handler,
    ));
    refresh.error(new HttpErrorResponse({ status: 401, statusText: 'Invalid refresh token' }));

    const results = await Promise.allSettled([first, second]);
    expect(results.every((result) => result.status === 'rejected')).toBe(true);
    expect(userAuth.refreshToken).toHaveBeenCalledTimes(1);
    expect(userAuth.logoutHub).toHaveBeenCalledTimes(1);
  });

  it('requests Hub login when an uncredentialed worker rejects the request', async () => {
    directory.list.mockReturnValue([
      { name: 'hub', role: 'hub', url: 'http://hub:5000', token: 'hub-secret' },
      { name: 'worker', role: 'worker', url: 'http://worker:5001' },
    ]);
    const coordinator = TestBed.inject(AuthRefreshCoordinator);
    const handler: HttpHandler = {
      handle: () => throwError(() => new HttpErrorResponse({ status: 401 })),
    };

    await expect(firstValueFrom(
      interceptor().intercept(new HttpRequest('GET', 'http://worker:5001/tasks'), handler),
    )).rejects.toBeTruthy();

    expect(coordinator.authRequired$.value).toBe('hub');
    expect(userAuth.refreshToken).not.toHaveBeenCalled();
  });
});
