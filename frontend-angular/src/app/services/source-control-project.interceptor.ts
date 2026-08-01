import {
  HttpErrorResponse,
  HttpHandler,
  HttpInterceptor,
  HttpRequest,
} from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { throwError } from 'rxjs';

import { ProjectContextService } from './project-context.service';

const PROJECT_BOUND_SOURCE_CONTROL_PATH =
  /^\/api\/source-control\/v1\/(?:connections|git-authorizations|indices)(?:\/|$)/;

@Injectable()
export class SourceControlProjectInterceptor implements HttpInterceptor {
  private readonly projectContext = inject(ProjectContextService);

  intercept(request: HttpRequest<unknown>, next: HttpHandler) {
    const path = request.url.replace(/^https?:\/\/[^/]+/i, '');
    if (!PROJECT_BOUND_SOURCE_CONTROL_PATH.test(path)) {
      return next.handle(request);
    }
    if (request.params.has('project_id')) {
      return next.handle(request);
    }
    const projectId = this.projectContext.selectedProjectId().trim();
    if (!projectId) {
      return throwError(() => new HttpErrorResponse({
        status: 422,
        statusText: 'Project Context Required',
        url: request.urlWithParams,
        error: { reason_code: 'project_context_required' },
      }));
    }
    return next.handle(request.clone({
      params: request.params.set('project_id', projectId),
    }));
  }
}
