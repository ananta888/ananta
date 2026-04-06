import { describe, it, expect, vi, beforeEach } from 'vitest';
import { HttpErrorResponse, HttpHandler, HttpRequest } from '@angular/common/http';
import { throwError } from 'rxjs';
import { ErrorInterceptor } from './error.interceptor';
import { TestBed } from '@angular/core/testing';
import { NotificationService } from './notification.service';
import { firstValueFrom } from 'rxjs';

describe('ErrorInterceptor', () => {
  let interceptor: ErrorInterceptor;
  const notify = vi.fn();

  beforeEach(() => {
    notify.mockReset();
    TestBed.configureTestingModule({
      providers: [
        ErrorInterceptor,
        { provide: NotificationService, useValue: { error: notify } },
      ],
    });
    interceptor = TestBed.inject(ErrorInterceptor);
  });

  function makeRequest() {
    return new HttpRequest('GET', 'http://hub:5000/teams');
  }

  function makeHandler(error: HttpErrorResponse): HttpHandler {
    return {
      handle: () => throwError(() => error),
    };
  }

  it('does not emit notification for 401 responses', async () => {
    const err = new HttpErrorResponse({
      status: 401,
      statusText: 'Unauthorized',
      url: 'http://hub:5000/teams',
      error: { detail: 'token expired' },
    });

    await expect(firstValueFrom(interceptor.intercept(makeRequest(), makeHandler(err)))).rejects.toBeTruthy();
    expect(notify).not.toHaveBeenCalled();
  });

  it('emits notification for non-401 responses', async () => {
    const err = new HttpErrorResponse({
      status: 500,
      statusText: 'Internal Server Error',
      url: 'http://hub:5000/teams',
      error: { detail: 'server exploded' },
    });

    await expect(firstValueFrom(interceptor.intercept(makeRequest(), makeHandler(err)))).rejects.toBeTruthy();
    expect(notify).toHaveBeenCalledTimes(1);
    const msg = String(notify.mock.calls[0][0] ?? '');
    expect(msg).toContain('API-Fehler (500)');
  });

  it('does not emit notification for transient GET status-0 responses', async () => {
    const req = new HttpRequest('GET', 'http://hub:5000/tasks/autopilot/status');
    const err = new HttpErrorResponse({
      status: 0,
      statusText: 'Unknown Error',
      url: 'http://hub:5000/tasks/autopilot/status',
    });

    await expect(firstValueFrom(interceptor.intercept(req, makeHandler(err)))).rejects.toBeTruthy();
    expect(notify).not.toHaveBeenCalled();
  });
});
