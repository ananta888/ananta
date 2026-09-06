import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { UserAuthService } from '../../../services/user-auth.service';
import { PersonaProfileApiClient } from './persona-profile-api.client';

describe('Persona profile HTTP client', () => {
  const scope = { hub: 'https://hub.test/', project: 'project', organization: 'org', kind: 'team', owner: 'team:1' } as const;
  let api: PersonaProfileApiClient;
  let http: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(),
      { provide: AgentDirectoryService, useValue: { list: () => [] } },
      { provide: UserAuthService, useValue: { token: null } },
    ] });
    api = TestBed.inject(PersonaProfileApiClient);
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());

  it('uses the project, organization and immutable owner route, with no automatic read retries', () => {
    const failed = vi.fn();
    api.current(scope).subscribe({ error: failed });
    const request = http.expectOne('https://hub.test/api/persona-media/v1/projects/project/organizations/org/profiles/team/team%3A1');
    expect(request.request.method).toBe('GET');
    request.flush({}, { status: 403, statusText: 'Denied' });
    expect(failed).toHaveBeenCalledOnce();
  });

  it('loads a private PNG blob, not an unauthenticated external URL or Meet publication', () => {
    const accept = vi.fn();
    api.preview(scope, 'image').subscribe(accept);
    const request = http.expectOne('https://hub.test/api/persona-media/v1/projects/project/images/image/preview');
    expect(request.request.responseType).toBe('blob');
    request.flush(new Blob(['synthetic-png'], { type: 'image/png' }));
    expect(accept).toHaveBeenCalledOnce();
  });

  it('rejects a mismatched preview type', () => {
    const failed = vi.fn();
    api.preview(scope, 'image').subscribe({ error: failed });
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/images/image/preview')
      .flush(new Blob(['synthetic-html'], { type: 'text/html' }));
    expect(failed).toHaveBeenCalledOnce();
  });

  it('queries bounded image pages without moving the opaque cursor into a URL', () => {
    api.images(scope, 'opaque-progress').subscribe();
    const request = http.expectOne('https://hub.test/api/persona-media/v1/projects/project/images/query');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ cursor: 'opaque-progress', limit: 20 });
    request.flush({ items: [], next_cursor: null, purpose: 'preview' });
  });
});
