import {
  HTTP_INTERCEPTORS,
  HttpClient,
  provideHttpClient,
  withInterceptorsFromDi,
} from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { describe, expect, it } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { SourceControlHubInterceptor } from './source-control-hub.interceptor';

describe('SourceControlHubInterceptor', () => {
  it('routes only relative Source-Control-v1 requests to the configured Hub', () => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
        {
          provide: AgentDirectoryService,
          useValue: {
            list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example.test' }],
          },
        },
        { provide: HTTP_INTERCEPTORS, useClass: SourceControlHubInterceptor, multi: true },
      ],
    });
    const http = TestBed.inject(HttpClient);
    const testing = TestBed.inject(HttpTestingController);

    http.get('/api/source-control/v1/workspaces').subscribe();
    http.get('https://other.example.test/api/source-control/v1/workspaces').subscribe();
    http.get('/api/projects').subscribe();

    testing.expectOne('https://hub.example.test/api/source-control/v1/workspaces').flush({});
    testing.expectOne('https://other.example.test/api/source-control/v1/workspaces').flush({});
    testing.expectOne('/api/projects').flush({});
    testing.verify();
  });

  it('fails closed before dispatch when no valid Hub origin is configured', async () => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
        { provide: HTTP_INTERCEPTORS, useClass: SourceControlHubInterceptor, multi: true },
      ],
    });
    const http = TestBed.inject(HttpClient);
    const testing = TestBed.inject(HttpTestingController);

    await expect(
      firstValueFrom(http.get('/api/source-control/v1/workspaces')),
    ).rejects.toMatchObject({ status: 503 });
    testing.expectNone('/api/source-control/v1/workspaces');
    testing.verify();
  });
});
