import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SourceControlApiError } from '../models/source-control-contracts';
import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { SourcesService } from './sources.service';

describe('SourcesService', () => {
  const core = {
    get: vi.fn(),
    post: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        SourcesService,
        { provide: HubApiCoreService, useValue: core },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ role: 'hub', name: 'hub', url: 'http://hub.test' }] },
        },
      ],
    });
  });

  it('loads and versions source descriptors through HubApiCore', async () => {
    core.get.mockReturnValue(of([{
      source_id: 'source-1',
      source_type: 'open_notebook',
      display_name: 'Notebook',
      trust_level: 'managed',
      enabled: true,
      latest_snapshot: null,
    }]));
    const service = TestBed.inject(SourcesService);

    const sources = await firstValueFrom(service.listSources());

    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/sources',
      'http://hub.test',
      undefined,
      false,
    );
    expect(sources[0].schema).toBe('source_descriptor.v1');
  });

  it.each([
    [0, 'offline'],
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not-found'],
    [409, 'conflict'],
    [422, 'unprocessable'],
    [429, 'rate-limited'],
    [500, 'server-error'],
  ] as const)('maps HTTP %s to %s without fabricating data', async (status, kind) => {
    core.get.mockReturnValue(throwError(() => ({ status })));
    const service = TestBed.inject(SourcesService);

    try {
      await firstValueFrom(service.listSources());
      throw new Error('expected request to fail');
    } catch (error) {
      expect(error).toBeInstanceOf(SourceControlApiError);
      expect((error as SourceControlApiError).kind).toBe(kind);
    }
  });
});
