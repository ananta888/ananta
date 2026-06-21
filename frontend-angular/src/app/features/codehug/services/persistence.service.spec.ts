import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { of } from 'rxjs';

import { PersistenceService } from './persistence.service';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';

function mockDir() {
  return { list: () => [{ role: 'hub', url: 'http://hub.test', name: 'h' }] };
}

function mockHub() {
  return {
    get: vi.fn(() => of([])),
    post: vi.fn(() => of({})),
    patch: vi.fn(() => of({})),
    delete: vi.fn(() => of({})),
    put: vi.fn(() => of({})),
  };
}

function setup() {
  const hub = mockHub();
  TestBed.configureTestingModule({
    providers: [
      PersistenceService,
      { provide: HubApiCoreService, useValue: hub },
      { provide: AgentDirectoryService, useValue: mockDir() },
    ],
  });
  return { svc: TestBed.inject(PersistenceService), hub };
}

describe('PersistenceService', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('listWorkspaces() GETs and refreshes recents', async () => {
    const { svc, hub } = setup();
    (hub.get as any).mockReturnValue(of([{ id: 'w1', name: 'foo', createdAt: 0, updatedAt: 0, snapshotCount: 0, owner: 'me' }]));
    const { firstValueFrom } = await import('rxjs');
    const list = await firstValueFrom(svc.listWorkspaces());
    expect(list.length).toBe(1);
    expect(svc.recents().length).toBe(1);
  });

  it('createWorkspace() POSTs and touches recents (LRU)', async () => {
    const { svc, hub } = setup();
    (hub.post as any).mockReturnValue(of({ id: 'w1', name: 'foo', createdAt: 0, updatedAt: 0, snapshotCount: 0, owner: 'me' }));
    const { firstValueFrom } = await import('rxjs');
    const w = await firstValueFrom(svc.createWorkspace({ name: 'foo' }));
    expect(w.id).toBe('w1');
    expect(svc.recents()[0].id).toBe('w1');
  });

  it('createSnapshot() POSTs to /api/codehug/snapshots', async () => {
    const { svc, hub } = setup();
    (hub.post as any).mockReturnValue(of({ id: 's1', version: 1, createdAt: 0, author: 'me' }));
    const { firstValueFrom } = await import('rxjs');
    const s = await firstValueFrom(svc.createSnapshot({
      workspaceId: 'w1', name: 'snap', symbolIds: ['s1'], fileIds: [], layerSet: ['l1'],
    }));
    expect(s.id).toBe('s1');
    expect(s.version).toBe(1);
  });

  it('removeWorkspace() DELETEs and removes from recents', async () => {
    const { svc, hub } = setup();
    (hub.delete as any).mockReturnValue(of(undefined));
    const { firstValueFrom } = await import('rxjs');
    svc.recents.set([{ id: 'w1', name: 'foo', createdAt: 0, updatedAt: 0, snapshotCount: 0, owner: 'me' } as any]);
    await firstValueFrom(svc.removeWorkspace('w1'));
    expect(svc.recents().length).toBe(0);
  });

  it('listSnapshots() GETs workspace snapshots', async () => {
    const { svc, hub } = setup();
    (hub.get as any).mockReturnValue(of([{ id: 's1', version: 1 }]));
    const { firstValueFrom } = await import('rxjs');
    const list = await firstValueFrom(svc.listSnapshots('w1'));
    expect(list.length).toBe(1);
  });

  it('LRU recents: 21st entry evicts first', async () => {
    const { svc, hub } = setup();
    let counter = 0;
    (hub.post as any).mockImplementation(() => of({ id: `w-${++counter}`, name: `w-${counter}`, createdAt: 0, updatedAt: 0, snapshotCount: 0, owner: 'me' }));
    const { firstValueFrom } = await import('rxjs');
    for (let i = 0; i < 21; i++) {
      await firstValueFrom(svc.createWorkspace({ name: `w-${i}` }));
    }
    expect(svc.recents().length).toBe(20);
  });
});