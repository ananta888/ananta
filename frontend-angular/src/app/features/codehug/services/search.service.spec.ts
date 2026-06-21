import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { of } from 'rxjs';

import { SearchService } from './search.service';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { CodeCompassService } from './code-compass.service';

function mockDir() {
  return { list: () => [{ role: 'hub', url: 'http://hub.test', name: 'h' }] };
}

function mockHub() {
  return {
    get: vi.fn(() => of({})),
    post: vi.fn(() => of({})),
    patch: vi.fn(() => of({})),
    delete: vi.fn(() => of({})),
    put: vi.fn(() => of({})),
  };
}

function mockCompass(detail: any) {
  return {
    getSymbolDetail: vi.fn(() => of(detail)),
  };
}

function setup(opts: { detail?: any; llmExplanation?: any } = {}) {
  const detail = opts.detail ?? {
    id: 'sym-1', name: 'foo', signature: 'foo(x): number',
    documentation: 'Berechnet den Foo-Wert aus dem uebergebenen Parameter x und liefert das Ergebnis als number zurueck.',
    filePath: 'a.py', callers: [], callees: [],
  };
  const hub = mockHub();
  const llm = opts.llmExplanation;
  if (llm) (hub.post as any).mockReturnValue(of(llm));
  TestBed.configureTestingModule({
    providers: [
      SearchService,
      { provide: HubApiCoreService, useValue: hub },
      { provide: AgentDirectoryService, useValue: mockDir() },
      { provide: CodeCompassService, useValue: mockCompass(detail) },
    ],
  });
  return { svc: TestBed.inject(SearchService), hub };
}

describe('SearchService', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('explain() returns heuristic when documentation is present', async () => {
    const { svc } = setup();
    const { firstValueFrom } = await import('rxjs');
    const e = await firstValueFrom(svc.explain('sym-1'));
    // Documentation "Documentation" (13) + suffix "Aufrufer" etc. = > 30
    expect(e.kind).toBe('heuristic');
    expect(e.summary.length).toBeGreaterThan(0);
    expect(e.llmEnhanced).toBe(false);
  });

  it('explain() calls LLM endpoint when documentation is too short', async () => {
    const { svc, hub } = setup({
      detail: { id: 'sym-1', name: 'foo', signature: 'foo()', documentation: '', filePath: 'a.py', callers: [], callees: [] },
      llmExplanation: { explanation: 'llm generated text', related: ['sym-2'] },
    });
    const { firstValueFrom } = await import('rxjs');
    const e = await firstValueFrom(svc.explain('sym-1'));
    expect(e.kind).toBe('llm');
    expect(e.summary).toBe('llm generated text');
    expect(e.llmEnhanced).toBe(true);
    expect(e.relatedSymbols).toEqual(['sym-2']);
    expect(hub.post).toHaveBeenCalled();
  });

  it('search() POSTs to /api/search', async () => {
    const { svc, hub } = setup();
    (hub.post as any).mockReturnValue(of([{ symbolId: 's1', name: 'foo' }]));
    const { firstValueFrom } = await import('rxjs');
    const list = await firstValueFrom(svc.search({ query: 'foo', mode: 'hybrid' }));
    expect(list.length).toBe(1);
  });

  it('search() caches result on second call', async () => {
    const { svc, hub } = setup();
    (hub.post as any).mockReturnValue(of([{ symbolId: 's1', name: 'foo' }]));
    const { firstValueFrom } = await import('rxjs');
    await firstValueFrom(svc.search({ query: 'cached', mode: 'hybrid' }));
    await firstValueFrom(svc.search({ query: 'cached', mode: 'hybrid' }));
    expect((hub.post as any).mock.calls.length).toBe(1);
  });

  it('invalidateCache() forces new fetch', async () => {
    const { svc, hub } = setup();
    (hub.post as any).mockReturnValue(of([{ symbolId: 's1', name: 'foo' }]));
    const { firstValueFrom } = await import('rxjs');
    await firstValueFrom(svc.search({ query: 'x', mode: 'hybrid' }));
    svc.invalidateCache();
    await firstValueFrom(svc.search({ query: 'x', mode: 'hybrid' }));
    expect((hub.post as any).mock.calls.length).toBe(2);
  });
});