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

  it('checks a voice through its own private reference route without fetching audio or models', () => {
    const reference = { kind: 'voice', artifact_id: 'voice:1', tenant_id: 'tenant', project_id: 'project', revision: 1,
      sha256: 'c'.repeat(64), classification: 'test_only' };
    const accept = vi.fn(); api.voice(scope, 'voice:1').subscribe(accept);
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/voices/voice%3A1/reference').flush({ reference });
    expect(accept).toHaveBeenCalledExactlyOnceWith(reference);
  });

  it('queries bounded voice metadata with the cursor in the body', () => {
    const accept = vi.fn(); api.voices(scope, 'v'.repeat(43)).subscribe(accept);
    const request = http.expectOne('https://hub.test/api/persona-media/v1/projects/project/voices/query');
    expect(request.request.method).toBe('POST'); expect(request.request.body).toEqual({ cursor: 'v'.repeat(43), limit: 20 });
    request.flush({ items: [], next_cursor: null, purpose: 'preview' });
    expect(accept).toHaveBeenCalledWith({ items: [], next_cursor: null, purpose: 'preview' });
  });

  it('rejects extra fields in a voice reference response instead of treating them as authority', () => {
    const failed = vi.fn(); api.voice(scope, 'voice').subscribe({ error: failed });
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/voices/voice/reference').flush({ reference: {
      kind: 'voice', artifact_id: 'voice', tenant_id: 'tenant', project_id: 'project', revision: 1,
      sha256: 'c'.repeat(64), classification: 'test_only',
    }, publish: true });
    expect(failed).toHaveBeenCalledOnce();
  });

  it.each([{ purpose: 'publish' }, { unknown: true }, { items: null }, { items: Array(21).fill(null) },
    { items: [null] }, { next_cursor: 'invalid' }, { items: [{ kind: 'video' }] }])('rejects malformed voice pages', patch => {
    const failed = vi.fn(); api.voices(scope, null).subscribe({ error: failed });
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/voices/query').flush({ items: [], next_cursor: null, purpose: 'preview', ...patch });
    expect(failed).toHaveBeenCalledOnce();
  });

  it.each(['duplicate', 'foreign-project', 'mixed-tenant'])('rejects %s voice metadata', failure => {
    const item = { kind: 'voice', artifact_id: 'voice', tenant_id: 'tenant', project_id: 'project', revision: 1,
      sha256: 'c'.repeat(64), classification: 'test_only' };
    const second = failure === 'duplicate' ? item : { ...item, artifact_id: 'second',
      ...(failure === 'foreign-project' ? { project_id: 'other' } : { tenant_id: 'other' }) };
    const failed = vi.fn(); api.voices(scope, null).subscribe({ error: failed });
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/voices/query').flush({ items: [item, second], next_cursor: null, purpose: 'preview' });
    expect(failed).toHaveBeenCalledOnce();
  });

  it.each([{ kind: 'image' }, { revision: true }, { sha256: 'invalid' }, { project_id: 'other' }, { artifact_id: 'other' }, { model: '/models/arbitrary' }])('rejects altered voice reference metadata', patch => {
    const failed = vi.fn(); api.voice(scope, 'voice').subscribe({ error: failed });
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/voices/voice/reference').flush({ reference: {
      kind: 'voice', artifact_id: 'voice', tenant_id: 'tenant', project_id: 'project', revision: 1,
      sha256: 'c'.repeat(64), classification: 'test_only', ...patch,
    } });
    expect(failed).toHaveBeenCalledOnce();
  });

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

  it('uses the video-specific authenticated reference path, never the image path', () => {
    const reference = { kind: 'video', artifact_id: 'clip:1', tenant_id: 'tenant', project_id: 'project', revision: 1, sha256: 'a'.repeat(64), classification: 'test_only' };
    const accept = vi.fn();
    api.video(scope, 'clip:1').subscribe(accept);
    const request = http.expectOne('https://hub.test/api/persona-media/v1/projects/project/videos/clip%3A1/reference');
    request.flush({ reference });
    expect(accept).toHaveBeenCalledWith(reference);
  });

  it('queries preview-only clip pages with opaque cursor in the request body', () => {
    const accept = vi.fn();
    api.videos(scope, 'v'.repeat(43)).subscribe(accept);
    const request = http.expectOne('https://hub.test/api/persona-media/v1/projects/project/videos/query');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ cursor: 'v'.repeat(43), limit: 20 });
    request.flush({ items: [], next_cursor: null, purpose: 'preview' });
    expect(accept).toHaveBeenCalledWith({ items: [], next_cursor: null, purpose: 'preview' });
  });

  it.each([
    { purpose: 'publish' }, { unknown: true }, { items: null }, { items: Array(21).fill(null) },
    { items: [null] }, { next_cursor: 'private-asset-id' }, { next_cursor: undefined },
    { items: [{ kind: 'image' }] },
  ])('rejects malformed or authority-broadening clip list responses', change => {
    const failed = vi.fn();
    api.videos(scope, null).subscribe({ error: failed });
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/videos/query').flush({
      items: [], next_cursor: null, purpose: 'preview', ...change,
    });
    expect(failed).toHaveBeenCalledOnce();
  });

  it.each(['duplicate', 'foreign-project', 'mixed-tenant'])('rejects %s clip pages', failure => {
    const item = { kind: 'video', artifact_id: 'clip', tenant_id: 'tenant', project_id: 'project', revision: 1,
      sha256: 'a'.repeat(64), classification: 'test_only' };
    const second = failure === 'duplicate' ? item : { ...item, artifact_id: 'clip2',
      ...(failure === 'foreign-project' ? { project_id: 'other' } : { tenant_id: 'other' }) };
    const failed = vi.fn();
    api.videos(scope, null).subscribe({ error: failed });
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/videos/query').flush({
      items: [item, second], next_cursor: null, purpose: 'preview',
    });
    expect(failed).toHaveBeenCalledOnce();
  });

  it('loads only a bounded private PNG for the clip preview', () => {
    const accept = vi.fn();
    api.videoPreview(scope, 'clip').subscribe(accept);
    const request = http.expectOne('https://hub.test/api/persona-media/v1/projects/project/videos/clip/preview');
    expect(request.request.responseType).toBe('blob');
    request.flush(new Blob(['synthetic-preview'], { type: 'image/png' }));
    expect(accept).toHaveBeenCalledOnce();
  });

  it.each([
    new Blob(['not-a-preview'], { type: 'video/mp4' }),
    new Blob([new Uint8Array(350_001)], { type: 'image/png' }),
  ])('rejects a video body or over-budget preview', body => {
    const failed = vi.fn();
    api.videoPreview(scope, 'clip').subscribe({ error: failed });
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/videos/clip/preview').flush(body);
    expect(failed).toHaveBeenCalledOnce();
  });

  it.each([
    { kind: 'image' }, { project_id: 'foreign' }, { artifact_id: 'other' }, { revision: true },
    { sha256: 'unverified' }, { classification: 'camera' }, { unknown: true },
  ])('rejects a mismatched clip reference before rendering its preview', change => {
    const failed = vi.fn();
    api.video(scope, 'clip').subscribe({ error: failed });
    http.expectOne('https://hub.test/api/persona-media/v1/projects/project/videos/clip/reference').flush({ reference: {
      kind: 'video', artifact_id: 'clip', tenant_id: 'tenant', project_id: 'project', revision: 1,
      sha256: 'a'.repeat(64), classification: 'test_only', ...change,
    } });
    expect(failed).toHaveBeenCalledOnce();
  });
});
