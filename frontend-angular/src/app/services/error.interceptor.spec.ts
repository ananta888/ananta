import { describe, it, expect, vi, beforeEach } from 'vitest';
import { HttpContext, HttpErrorResponse, HttpHandler, HttpRequest } from '@angular/common/http';
import { throwError } from 'rxjs';
import { ErrorInterceptor } from './error.interceptor';
import { TestBed } from '@angular/core/testing';
import { NotificationService } from './notification.service';
import { firstValueFrom } from 'rxjs';
import {
  SUPPRESS_GLOBAL_ERROR_NOTIFICATION,
  SUPPRESS_GLOBAL_NOT_FOUND_NOTIFICATION,
} from './error-request-context';

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

  it('renders a nested source-control error code instead of an object string', async () => {
    const err = new HttpErrorResponse({
      status: 400,
      statusText: 'Bad Request',
      url: 'http://hub:5000/api/source-control/v1/connections/conn-1/scan',
      error: { error: { code: 'remote_source_payload_required' } },
    });

    await expect(firstValueFrom(interceptor.intercept(makeRequest(), makeHandler(err)))).rejects.toBe(err);
    expect(notify).toHaveBeenCalledWith('API-Fehler (400): remote_source_payload_required');
  });

  it('rethrows locally handled request failures without a global notification', async () => {
    const request = new HttpRequest(
      'POST',
      'http://hub:5000/v1/voice/live-runs/run-a/stop',
      {},
      {
        context: new HttpContext().set(SUPPRESS_GLOBAL_ERROR_NOTIFICATION, true),
      },
    );
    const err = new HttpErrorResponse({
      status: 409,
      statusText: 'Conflict',
      url: request.url,
      error: {
        data: {
          error: {
            code: 'voice_live_run.segments_in_flight',
            retriable: true,
          },
        },
      },
    });

    await expect(firstValueFrom(interceptor.intercept(request, makeHandler(err)))).rejects.toBe(err);
    expect(notify).not.toHaveBeenCalled();
    expect((err as any).__anantaHandledByInterceptor).toBe(true);
  });

  it('still emits a global notification for an unmarked conflict', async () => {
    const request = new HttpRequest('POST', 'http://hub:5000/v1/configuration', {});
    const err = new HttpErrorResponse({
      status: 409,
      statusText: 'Conflict',
      url: request.url,
      error: { detail: 'configuration conflict' },
    });

    await expect(firstValueFrom(interceptor.intercept(request, makeHandler(err)))).rejects.toBe(err);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(String(notify.mock.calls[0][0] ?? '')).toContain('API-Fehler (409)');
  });

  it('suppresses only a marked, locally handled not-found response', async () => {
    const request = new HttpRequest(
      'DELETE',
      'http://hub:5000/v1/voice/streams/preview-a',
      undefined,
      {
        context: new HttpContext().set(SUPPRESS_GLOBAL_NOT_FOUND_NOTIFICATION, true),
      },
    );
    const err = new HttpErrorResponse({
      status: 404,
      statusText: 'Not Found',
      url: request.url,
      error: { code: 'voice_stream.not_found' },
    });

    await expect(firstValueFrom(interceptor.intercept(request, makeHandler(err)))).rejects.toBe(err);
    expect(notify).not.toHaveBeenCalled();
    expect((err as any).__anantaHandledByInterceptor).toBe(true);
  });

  it('still emits a global notification for other failures on a marked request', async () => {
    const request = new HttpRequest(
      'DELETE',
      'http://hub:5000/v1/voice/streams/preview-a',
      undefined,
      {
        context: new HttpContext().set(SUPPRESS_GLOBAL_NOT_FOUND_NOTIFICATION, true),
      },
    );
    const err = new HttpErrorResponse({
      status: 500,
      statusText: 'Internal Server Error',
      url: request.url,
      error: { detail: 'cleanup failed' },
    });

    await expect(firstValueFrom(interceptor.intercept(request, makeHandler(err)))).rejects.toBe(err);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(String(notify.mock.calls[0][0] ?? '')).toContain('API-Fehler (500)');
  });

  it('still emits a global notification for an unmarked not-found response', async () => {
    const request = new HttpRequest('DELETE', 'http://hub:5000/v1/voice/streams/missing');
    const err = new HttpErrorResponse({
      status: 404,
      statusText: 'Not Found',
      url: request.url,
      error: { code: 'voice_stream.not_found' },
    });

    await expect(firstValueFrom(interceptor.intercept(request, makeHandler(err)))).rejects.toBe(err);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(String(notify.mock.calls[0][0] ?? '')).toContain('API-Fehler (404)');
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
