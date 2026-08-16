import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { firstValueFrom, of, throwError } from 'rxjs';

import { SourceControlV1ApiClient } from '../../../services/source-control-v1-api.client';
import { CodeCompassService } from './code-compass.service';
import { ChServiceError } from '../models/codehug.models';

function mockSourceControl() {
  return {
    listConnections: vi.fn(),
    queryConnection: vi.fn(),
  };
}

const CONNECTION_PAGE = {
  items: [{
    connection_id: 'p1',
    connection: { display_name: 'Test' },
    active_index: { updated_at: '2026-01-01T00:00:00Z' },
    index: { status: 'completed', language_breakdown: { ts: 4 }, framework_signals: ['angular'], module_count: 3, file_count: 5, symbol_count: 7 },
    connector_type: 'registered_workspace',
  }],
  next_cursor: null,
};

describe('CodeCompassService', () => {
  let service: CodeCompassService;
  let sourceControl: ReturnType<typeof mockSourceControl>;

  beforeEach(() => {
    sourceControl = mockSourceControl();
    sourceControl.listConnections.mockReturnValue(of(CONNECTION_PAGE));
    sourceControl.queryConnection.mockImplementation(() => of({}));
    TestBed.configureTestingModule({
      providers: [
        CodeCompassService,
        { provide: SourceControlV1ApiClient, useValue: sourceControl },
      ],
    });
    service = TestBed.inject(CodeCompassService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('getProject: resolves normalized project model from active connection list', async () => {
    const project = await firstValueFrom(service.getProject('p1'));
    expect(project.id).toBe('p1');
    expect(project.name).toBe('Test');
    expect(project.indexStatus).toBe('completed');
  });

  it('getProject: rejects unknown project ids from the connection list', async () => {
    sourceControl.listConnections.mockReturnValueOnce(of(CONNECTION_PAGE));
    await expect(firstValueFrom(service.getProject('missing'))).rejects.toThrow(ChServiceError);
  });

  it('resolveContext: passes request description as query payload', async () => {
    sourceControl.queryConnection.mockReturnValueOnce(of({
      payload: {
        suggestions: [{ symbol_id: 'symbol-1', file_path: 'file.py', relevance: 0.9, reason: 'top hit' }],
        resolved_symbols: [{ symbolId: 'symbol-1', filePath: 'file.py', name: 'sym' }],
        estimated_token_count: 0,
      },
    }));
    const resp = await firstValueFrom(service.resolveContext({ projectId: 'p1', taskDescription: 'find bug', maxSuggestions: 5 }));
    expect(sourceControl.queryConnection).toHaveBeenCalledWith('p1', {
      query: 'find bug',
      limit: 5,
    });
    expect(resp.estimatedTokenCount).toBe(0);
  });

  it('searchSymbols: forwards query body unmodified and ignores URL encoding concerns', async () => {
    sourceControl.queryConnection.mockReturnValueOnce(of({
      symbols: [{ id: 's1', file_path: 'a.ts', name: 'Alpha', line_start: 1, line_end: 2 }],
      total_matches: 1,
    }));
    await firstValueFrom(service.searchSymbols({ projectId: 'p1', query: 'foo bar' }));
    expect(sourceControl.queryConnection).toHaveBeenCalledWith('p1', { query: 'foo bar' });
  });

  it('healthCheck: returns true when source-control list endpoint is reachable', async () => {
    const ok = await firstValueFrom(service.healthCheck());
    expect(ok).toBe(true);
  });

  it('healthCheck: returns false on error (resilient)', async () => {
    sourceControl.listConnections = vi.fn(() => throwError(() => new Error('net')));
    const ok = await firstValueFrom(service.healthCheck());
    expect(ok).toBe(false);
  });
});
