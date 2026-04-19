import { HttpErrorResponse, HttpHandler, HttpRequest, HttpResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of, throwError } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { AuthInterceptor } from './auth.interceptor';
import { UserAuthService } from './user-auth.service';

describe('AuthInterceptor', () => {
  let directory: { list: ReturnType<typeof vi.fn> };
  let userAuth: {
    token: string | null;
    refreshToken: ReturnType<typeof vi.fn>;
    logout: ReturnType<typeof vi.fn>;
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
      refreshToken: vi.fn(() => of({ access_token: 'new-user-token' })),
      logout: vi.fn(),
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
    userAuth.refreshToken.mockReturnValueOnce(throwError(() => new Error('refresh failed')));
    const handler: HttpHandler = {
      handle: vi.fn((request: HttpRequest<unknown>) => (
        throwError(() => new HttpErrorResponse({ status: 401, url: request.url }))
      )),
    };

    await expect(
      firstValueFrom(interceptor().intercept(new HttpRequest('GET', 'http://hub:5000/tasks'), handler)),
    ).rejects.toBeTruthy();

    expect(userAuth.refreshToken).toHaveBeenCalledTimes(1);
    expect(userAuth.logout).toHaveBeenCalledTimes(1);
  });
});
