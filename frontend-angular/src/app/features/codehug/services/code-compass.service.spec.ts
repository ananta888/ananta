import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { firstValueFrom, of, throwError } from 'rxjs';

import { CodeCompassService } from './code-compass.service';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { ChServiceError } from '../models/codehug.models';

/**
 * Test-Helper: HubApiCoreService.get/post mocken.
 */
function mockHubCore(responses: Record<string, any> = {}) {
  return {
    get: vi.fn((url: string) => {
      const key = Object.keys(responses).find(k => url.includes(k));
      if (!key) throw new Error(`Unmocked GET: ${url}`);
      return of(responses[key]);
    }),
    post: vi.fn((url: string, body: any) => {
      const key = Object.keys(responses).find(k => url.includes(k));
      if (!key) throw new Error(`Unmocked POST: ${url}`);
      return of(responses[key]);
    }),
    patch: vi.fn(),
    delete: vi.fn(),
  };
}

function mockDir(url = 'http://hub.test') {
  return {
    list: vi.fn(() => [{ role: 'hub', url, name: 'test-hub' }]),
  };
}

describe('CodeCompassService', () => {
  let service: CodeCompassService;
  let hubCore: ReturnType<typeof mockHubCore>;

  beforeEach(() => {
    hubCore = mockHubCore({
      '/api/codecompass/projects/': { id: 'p1', name: 'Test', root_path: '/tmp', index_status: 'complete' },
      '/api/codecompass/reload-context': { suggestions: [], resolved_symbols: [], estimated_token_count: 0 },
      '/api/codecompass/query': { symbols: [], total_matches: 0 },
    });
    TestBed.configureTestingModule({
      providers: [
        CodeCompassService,
        { provide: HubApiCoreService, useValue: hubCore },
        { provide: AgentDirectoryService, useValue: mockDir() },
      ],
    });
    service = TestBed.inject(CodeCompassService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('throws ChServiceError when no hub agent is registered', () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        CodeCompassService,
        { provide: HubApiCoreService, useValue: hubCore },
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
      ],
    });
    const s = TestBed.inject(CodeCompassService);
    expect(() => s.getProject('p1')).toThrow(ChServiceError);
  });

  it('getProject: calls correct endpoint and returns normalized model', async () => {
    const project = await firstValueFrom(service.getProject('p1'));
    expect(project.id).toBe('p1');
    expect(project.name).toBe('Test');
    expect(project.rootPath).toBe('/tmp');
  });

  it('resolveContext: passes task_description in request body', async () => {
    const resp = await firstValueFrom(service.resolveContext({ projectId: 'p1', taskDescription: 'find bug' }));
    expect(hubCore.post).toHaveBeenCalled();
    const callArgs = (hubCore.post as any).mock.calls[0];
    expect(callArgs[1].request.description).toBe('find bug');
    expect(resp.estimatedTokenCount).toBe(0);
  });

  it('searchSymbols: encodes query and limits', async () => {
    await firstValueFrom(service.searchSymbols({ projectId: 'p1', query: 'foo bar', limit: 5 }));
    const callArgs = (hubCore.get as any).mock.calls[0];
    expect(callArgs[0]).toContain('seed=foo+bar');
    expect(callArgs[0]).toContain('limit=5');
  });

  it('healthCheck: returns true on ok status', async () => {
    hubCore.get = vi.fn(() => of({ status: 'ok' }));
    const ok = await firstValueFrom(service.healthCheck());
    expect(ok).toBe(true);
  });

  it('healthCheck: returns false on error (resilient)', async () => {
    hubCore.get = vi.fn(() => throwError(() => new Error('net')));
    const ok = await firstValueFrom(service.healthCheck());
    expect(ok).toBe(false);
  });
});