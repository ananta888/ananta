import {
  HttpErrorResponse,
  HttpHandler,
  HttpInterceptor,
  HttpRequest,
} from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { throwError } from 'rxjs';

import {
  AgentDirectoryService,
  normalizeHubOrigin,
} from './agent-directory.service';

const SOURCE_CONTROL_V1_PATH = /^\/api\/source-control\/v1(?:\/|$)/;

/** Routes relative Source-Control-v1 calls through the configured Hub origin. */
@Injectable()
export class SourceControlHubInterceptor implements HttpInterceptor {
  private readonly directory = inject(AgentDirectoryService);

  intercept(request: HttpRequest<unknown>, next: HttpHandler) {
    if (!SOURCE_CONTROL_V1_PATH.test(request.url)) {
      return next.handle(request);
    }

    const hub = this.directory.list().find((agent) => agent.role === 'hub')
      ?? this.directory.list().find((agent) => agent.name === 'hub');
    const hubOrigin = normalizeHubOrigin(hub?.url ?? '');
    if (!hubOrigin) {
      return throwError(() => new HttpErrorResponse({
        status: 503,
        statusText: 'Hub Endpoint Required',
        url: request.urlWithParams,
        error: { reason_code: 'hub_endpoint_required' },
      }));
    }

    return next.handle(request.clone({ url: `${hubOrigin}${request.url}` }));
  }
}
