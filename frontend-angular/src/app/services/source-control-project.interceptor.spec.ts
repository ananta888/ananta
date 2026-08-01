import {
  HTTP_INTERCEPTORS,
  HttpClient,
  provideHttpClient,
  withInterceptorsFromDi,
} from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { describe, expect, it } from 'vitest';

import { ProjectContextService } from './project-context.service';
import { SourceControlProjectInterceptor } from './source-control-project.interceptor';

describe('SourceControlProjectInterceptor', () => {
  it('adds only the active project query to project-bound Source-Control requests', () => {
    const selectedProjectId = signal('project alpha');
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
        { provide: ProjectContextService, useValue: { selectedProjectId } },
        { provide: HTTP_INTERCEPTORS, useClass: SourceControlProjectInterceptor, multi: true },
      ],
    });
    const http = TestBed.inject(HttpClient);
    const testing = TestBed.inject(HttpTestingController);

    http.get('/api/source-control/v1/connections').subscribe();
    http.get('/api/source-control/v1/git-authorizations/health').subscribe();
    http.post('/api/source-control/v1/indices/index-1/activate', {}).subscribe();
    http.get('/api/projects').subscribe();

    const connectionRequest = testing.expectOne(
      '/api/source-control/v1/connections?project_id=project%20alpha',
    );
    expect(connectionRequest.request.params.keys()).toEqual(['project_id']);
    connectionRequest.flush([]);

    const gitAuthorizationRequest = testing.expectOne(
      '/api/source-control/v1/git-authorizations/health?project_id=project%20alpha',
    );
    expect(gitAuthorizationRequest.request.params.keys()).toEqual(['project_id']);
    gitAuthorizationRequest.flush({});

    const indexRequest = testing.expectOne(
      '/api/source-control/v1/indices/index-1/activate?project_id=project%20alpha',
    );
    expect(indexRequest.request.params.keys()).toEqual(['project_id']);
    indexRequest.flush({});

    const unrelatedRequest = testing.expectOne('/api/projects');
    expect(unrelatedRequest.request.params.has('project_id')).toBe(false);
    unrelatedRequest.flush([]);
    testing.verify();
  });

  it('fails closed before dispatch when source-control has no active project', async () => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
        {
          provide: ProjectContextService,
          useValue: { selectedProjectId: signal('') },
        },
        { provide: HTTP_INTERCEPTORS, useClass: SourceControlProjectInterceptor, multi: true },
      ],
    });
    const http = TestBed.inject(HttpClient);
    const testing = TestBed.inject(HttpTestingController);
    const response = firstValueFrom(http.get('/api/source-control/v1/connections'));

    await expect(response).rejects.toMatchObject({ status: 422 });
    testing.expectNone('/api/source-control/v1/connections');
    testing.verify();
  });
});
