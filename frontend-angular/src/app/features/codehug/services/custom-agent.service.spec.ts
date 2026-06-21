import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { of, throwError } from 'rxjs';

import { CustomAgentService } from './custom-agent.service';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';

function mockHub() {
  return {
    get: vi.fn(() => of([])),
    post: vi.fn(() => of({})),
    patch: vi.fn(() => of({})),
    delete: vi.fn(() => of({})),
    put: vi.fn(() => of({})),
  };
}

function mockDir() {
  return { list: () => [{ role: 'hub', url: 'http://hub.test', name: 'h' }] };
}

function setup() {
  const hub = mockHub();
  TestBed.configureTestingModule({
    providers: [
      CustomAgentService,
      { provide: HubApiCoreService, useValue: hub },
      { provide: AgentDirectoryService, useValue: mockDir() },
    ],
  });
  return { svc: TestBed.inject(CustomAgentService), hub };
}

describe('CustomAgentService', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('list() GETs /api/custom-agents', async () => {
    const { svc, hub } = setup();
    (hub.get as any).mockReturnValue(of([{ id: 'a1', name: 'foo', description: 'd', systemPrompt: 'p' }]));
    const { firstValueFrom } = await import('rxjs');
    const list = await firstValueFrom(svc.list());
    expect(list.length).toBe(1);
    expect(list[0].id).toBe('a1');
  });

  it('create() POSTs to /api/custom-agents', async () => {
    const { svc, hub } = setup();
    (hub.post as any).mockReturnValue(of({ id: 'a1', name: 'foo' }));
    const { firstValueFrom } = await import('rxjs');
    const r = await firstValueFrom(svc.create({ name: 'foo', description: 'd', systemPrompt: 'p' }));
    expect(r.id).toBe('a1');
  });

  it('update() PUTs to /api/custom-agents/:id', async () => {
    const { svc, hub } = setup();
    (hub.put as any).mockReturnValue(of({ id: 'a1', name: 'foo2' }));
    const { firstValueFrom } = await import('rxjs');
    const r = await firstValueFrom(svc.update('a1', { name: 'foo2', description: 'd', systemPrompt: 'p' }));
    expect(r.name).toBe('foo2');
  });

  it('remove() DELETEs /api/custom-agents/:id', async () => {
    const { svc, hub } = setup();
    (hub.delete as any).mockReturnValue(of(undefined));
    const { firstValueFrom } = await import('rxjs');
    await firstValueFrom(svc.remove('a1'));
    expect(hub.delete).toHaveBeenCalled();
  });

  it('run() POSTs to /:id/run with prompt', async () => {
    const { svc, hub } = setup();
    (hub.post as any).mockReturnValue(of({ id: 'run-1', status: 'succeeded', steps: [] }));
    const { firstValueFrom } = await import('rxjs');
    const r = await firstValueFrom(svc.run('a1', 'explain foo'));
    expect(r.id).toBe('run-1');
  });

  it('ping() returns false on error', async () => {
    const { svc, hub } = setup();
    (hub.get as any).mockReturnValue(throwError(() => new Error('net')));
    const { firstValueFrom } = await import('rxjs');
    const ok = await firstValueFrom(svc.ping());
    expect(ok).toBe(false);
  });
});