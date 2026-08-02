import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { of, throwError } from 'rxjs';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { CodehugWikiGraphComponent } from './codehug-wiki-graph.component';
import { InternalsService } from '../services/internals.service';


beforeAll(async () => {
  await ɵresolveComponentResources((resource) =>
    readFile(new URL(String(resource), import.meta.url), 'utf8'),
  );
});


describe('CodehugWikiGraphComponent', () => {
  const service = {
    listKnowledgeIndexes: vi.fn(),
    getCodeCompassGraph: vi.fn(),
    getWikiGraphStatus: vi.fn(() => of(null)),
  };

  beforeEach(async () => {
    vi.clearAllMocks();
    await TestBed.configureTestingModule({
      imports: [CodehugWikiGraphComponent],
      providers: [{ provide: InternalsService, useValue: service }],
    }).compileComponents();
  });

  it('loads the first active project connection without an unbound self graph', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
      source_scope: 'connection',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of({
      metadata: { node_count: 0, edge_count: 0 },
      nodes: [],
      edges: [],
    }));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.selectedConnectionId()).toBe('conn-example');
    expect(service.getCodeCompassGraph).toHaveBeenCalledWith('conn-example');
    expect(fixture.componentInstance.error()).not.toContain('ungebundene Self-Graph');
  });

  it('explains that a graph-capable profile is required for an empty index', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of({
      metadata: { node_count: 0, edge_count: 0 },
      nodes: [],
      edges: [],
    }));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.error()).toContain('Deep Code');
  });

  it('reports a missing project index without requesting an unbound graph', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([]));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(service.getCodeCompassGraph).not.toHaveBeenCalled();
    expect(fixture.componentInstance.error()).toContain('aktivem Index');
  });

  it('contains project-index loading failures', () => {
    service.listKnowledgeIndexes.mockReturnValue(
      throwError(() => new Error('unavailable')),
    );

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.loading()).toBe(false);
    expect(fixture.componentInstance.error()).toContain('nicht geladen');
  });
});
