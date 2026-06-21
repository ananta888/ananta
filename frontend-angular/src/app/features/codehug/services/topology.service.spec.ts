import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { of, throwError } from 'rxjs';

import { TopologyService } from './topology.service';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { HubControlCenterApiClient } from '../../control-center/services/hub-control-center-api.client';
import { ChServiceError } from '../models/codehug.models';

function mockHubCore() {
  return {
    get: vi.fn(() => of({})),
    post: vi.fn(() => of({})),
    patch: vi.fn(() => of({})),
    delete: vi.fn(),
  };
}

function mockDir() {
  return { list: () => [{ role: 'hub', url: 'http://hub.test', name: 'h' }] };
}

function mockCc() {
  return {
    listWorkers: vi.fn(() => of({ items: [
      { id: 'sgpt-1', runtime: 'sgpt', health: 'healthy', capabilities: ['read'], boundary: 'local-only' },
      { id: 'opencode-det', runtime: 'opencode', health: 'healthy', capabilities: ['read', 'write'], boundary: 'cloud-allowed' },
    ], count: 2 })),
    listSessionToolCalls: vi.fn(() => of({ items: [{ id: 'tc-1', tool_name: 'read_file' }], count: 1 })),
    listSessionPolicyDecisions: vi.fn(() => of({ items: [], count: 0 })),
    createEventStreamToken: vi.fn(() => of({ stream_token: 'tk', expires_at: 9999, ttl_seconds: 60 })),
  };
}

describe('TopologyService', () => {
  let svc: TopologyService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        TopologyService,
        { provide: HubApiCoreService, useValue: mockHubCore() },
        { provide: AgentDirectoryService, useValue: mockDir() },
        { provide: HubControlCenterApiClient, useValue: mockCc() },
      ],
    });
    svc = TestBed.inject(TopologyService);
  });

  it('throws ChServiceError when no hub agent registered', () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        TopologyService,
        { provide: HubApiCoreService, useValue: mockHubCore() },
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
        { provide: HubControlCenterApiClient, useValue: mockCc() },
      ],
    });
    const s = TestBed.inject(TopologyService);
    expect(() => s.getTopology()).toThrow(ChServiceError);
  });

  it('getTopology: includes hubs, workers and connections', async () => {
    const { firstValueFrom } = await import('rxjs');
    const topo = await firstValueFrom(svc.getTopology());
    expect(topo.hubs.length).toBe(1);
    expect(topo.workers.length).toBe(2);
    expect(topo.connections.length).toBe(2);
    expect(topo.workers[0].cliBackend).toBe('sgpt');
    expect(topo.workers[1].cliBackend).toBe('opencode');
  });

  it('detectCliBackend: maps ids to backend names', async () => {
    const { firstValueFrom } = await import('rxjs');
    const topo = await firstValueFrom(svc.getTopology());
    expect(topo.workers[0].cliBackend).toBe('sgpt');
    expect(topo.workers[1].cliBackend).toBe('opencode');
  });

  it('healthCheck returns true when workers endpoint responds', async () => {
    const { firstValueFrom } = await import('rxjs');
    const ok = await firstValueFrom(svc.healthCheck());
    expect(ok).toBe(true);
  });

  it('healthCheck returns false on error', async () => {
    const cc = TestBed.inject(HubControlCenterApiClient) as any;
    cc.listWorkers = vi.fn(() => throwError(() => new Error('net')));
    const { firstValueFrom } = await import('rxjs');
    const ok = await firstValueFrom(svc.healthCheck());
    expect(ok).toBe(false);
  });

  it('getSessionToolCalls unwraps items', async () => {
    const { firstValueFrom } = await import('rxjs');
    const calls = await firstValueFrom(svc.getSessionToolCalls('sess-1'));
    expect(calls.length).toBe(1);
    expect(calls[0].id).toBe('tc-1');
    expect((calls[0] as any).tool_name).toBe('read_file');
  });

  it('createStreamToken extracts token + expires_at', async () => {
    const { firstValueFrom } = await import('rxjs');
    const t = await firstValueFrom(svc.createStreamToken());
    expect(t.token).toBe('tk');
    expect(t.expiresAt).toBe(9999);
  });
});