import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { of, throwError } from 'rxjs';

import { RefactoringService } from './refactoring.service';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { PolicyService } from './policy.service';

function mockHub() {
  return {
    get: vi.fn(() => of({})),
    post: vi.fn(() => of({})),
    patch: vi.fn(() => of({})),
    delete: vi.fn(() => of({})),
    put: vi.fn(() => of({})),
  };
}

function mockDir() {
  return { list: () => [{ role: 'hub', url: 'http://hub.test', name: 'h' }] };
}

function setup(overrides: { writeModeActive?: boolean; hub?: any } = {}) {
  const hub = overrides.hub ?? mockHub();
  const policy: any = {
    writeModeActive: () => overrides.writeModeActive ?? false,
    writeMode: () => 'read-only',
  };
  TestBed.configureTestingModule({
    providers: [
      RefactoringService,
      { provide: HubApiCoreService, useValue: hub },
      { provide: AgentDirectoryService, useValue: mockDir() },
      { provide: PolicyService, useValue: policy },
    ],
  });
  return { svc: TestBed.inject(RefactoringService), hub, policy };
}

describe('RefactoringService', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('propose() POSTs to /api/refactoring/propose and unwraps', async () => {
    const { svc, hub } = setup();
    (hub.post as any).mockReturnValue(of([
      { id: 'r1', kind: 'rename_symbol', title: 'foo', description: 'd', affected_files: ['x.py'], affected_symbols: ['a'], generated_by: 'deterministic', confidence: 0.95, status: 'open' },
    ]));
    const { firstValueFrom } = await import('rxjs');
    const list = await firstValueFrom(svc.propose({ workspacePath: '/ws' }));
    expect(list.length).toBe(1);
    expect(list[0].id).toBe('r1');
    expect(list[0].affectedFiles).toEqual(['x.py']);
  });

  it('previewDiff() GETs the diff endpoint', async () => {
    const { svc, hub } = setup();
    (hub.get as any).mockReturnValue(of({ proposalId: 'r1', hunks: [], validation: { syntaxOk: true, typeCheckOk: true, linterOk: true, diagnostics: [] }, generatedAt: 123 }));
    const { firstValueFrom } = await import('rxjs');
    const d = await firstValueFrom(svc.previewDiff('r1'));
    expect(d.proposalId).toBe('r1');
    expect(d.validation.syntaxOk).toBe(true);
  });

  it('apply() is BLOCKED when write-mode is not active', async () => {
    const { svc } = setup({ writeModeActive: false });
    const { firstValueFrom } = await import('rxjs');
    await expect(firstValueFrom(svc.apply('r1'))).rejects.toThrow(/write-armed/);
  });

  it('apply() succeeds when write-mode is active', async () => {
    const { svc, hub } = setup({ writeModeActive: true });
    (hub.post as any).mockReturnValue(of({ proposalId: 'r1', status: 'applied', appliedFiles: ['x.py'], testGate: { ran: true, passed: true, diagnostics: [] }, message: 'ok' }));
    const { firstValueFrom } = await import('rxjs');
    const r = await firstValueFrom(svc.apply('r1'));
    expect(r.status).toBe('applied');
  });

  it('dismiss() DELETEs the proposal', async () => {
    const { svc, hub } = setup();
    (hub.delete as any).mockReturnValue(of(undefined));
    const { firstValueFrom } = await import('rxjs');
    await firstValueFrom(svc.dismiss('r1'));
    expect(hub.delete).toHaveBeenCalled();
  });

  it('propose() maps 422 to validation_error', async () => {
    const { svc, hub } = setup();
    (hub.post as any).mockReturnValue(throwError(() => ({ status: 422, message: 'bad' })));
    const { firstValueFrom } = await import('rxjs');
    await expect(firstValueFrom(svc.propose({ workspacePath: '/ws' }))).rejects.toMatchObject({ code: 'validation_error' });
  });
});