/**
 * One screen over two subsystems.
 *
 * The failure that matters here is asymmetric: organisations and teams come
 * from different services, and one being unreachable must cost only its own
 * cards. What is pinned is that, plus that opening a card lands on the right
 * level with the right thing loaded.
 */

import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { of, throwError } from 'rxjs';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { ProjectContextService } from '../../../services/project-context.service';
import { OrganizationApiClient } from '../../organizations/services/organization-api.client';
import { VisualProcessApiService, type VpGraph } from '../../visual-process/visual-process-api.service';
import { CaseFlowOverviewComponent } from './caseflow-overview.component';

beforeAll(async () => {
  await ɵresolveComponentResources(async resource => {
    const name = resource.split('/').at(-1) || resource;
    const candidates = [
      new URL(resource, import.meta.url),
      resolve(process.cwd(), 'src/app/features/caseflow/team-builder', name),
      resolve(process.cwd(), 'src/app/features/caseflow/agent-canvas', name),
    ];
    for (const candidate of candidates) {
      try {
        return await readFile(candidate, 'utf8');
      } catch {
        // Angular reports relative URLs only; try the owning directory.
      }
    }
    throw new Error(`Component resource not found: ${resource}`);
  });
});

const HUB = 'http://hub.test:5000';
const PROJECT = 'project-1';

const ORGANIZATION = {
  id: 'org-1',
  key: 'produkt',
  title: 'Produktorganisation',
  lifecycle: 'draft',
  team_count: 4,
  unit_count: 2,
};

const TEAM = { id: 'g-1', name: 'Mein Team', description: 'Drei Agenten', tags: [] };

function graph(): VpGraph {
  return {
    id: 'g-1',
    name: 'Mein Team',
    description: '',
    version: '1.0.0',
    tags: [],
    steps: [
      {
        id: 'mara',
        label: 'Mara',
        kind: 'coding',
        role: 'developer',
        io: { inputs: [], outputs: [] },
        position: { x: 1, y: 1 },
        policy_hints: [],
        gate: false,
      },
    ],
    edges: [],
  } as unknown as VpGraph;
}

function topology(nodes: readonly Record<string, unknown>[]) {
  return {
    organization_id: 'org-1',
    definition_revision: 'r1',
    snapshot_hash: 'h',
    nodes,
    edges: [],
    runtime_overlay: null,
    diagnostics: [],
    limits: {},
    next_cursor: null,
    truncated: false,
  };
}

let organizations: {
  listOrganizations: ReturnType<typeof vi.fn>;
  topology: ReturnType<typeof vi.fn>;
};
let api: {
  listSavedGraphs: ReturnType<typeof vi.fn>;
  loadSavedGraph: ReturnType<typeof vi.fn>;
  saveGraph: ReturnType<typeof vi.fn>;
  getWorkflowStatus: ReturnType<typeof vi.fn>;
};
let projectId: string;

function mount() {
  const fixture = TestBed.createComponent(CaseFlowOverviewComponent);
  fixture.detectChanges();
  return fixture;
}

function text(fixture: ReturnType<typeof mount>): string {
  return fixture.nativeElement.textContent as string;
}

function cards(fixture: ReturnType<typeof mount>) {
  return fixture.debugElement.queryAll(By.css('.ov-card'));
}

beforeEach(() => {
  projectId = PROJECT;
  organizations = {
    listOrganizations: vi.fn().mockReturnValue(of({ items: [ORGANIZATION], next_cursor: null })),
    topology: vi.fn().mockReturnValue(
      of(topology([
        { id: 'n-1', stable_key: 'unit.a', kind: 'coordination_unit', label: 'Plattform', depth: 1 },
        { id: 'n-2', stable_key: 'team.a', kind: 'team', label: 'Alpha', depth: 2 },
      ])),
    ),
  };
  api = {
    listSavedGraphs: vi.fn().mockReturnValue(of([TEAM])),
    loadSavedGraph: vi.fn().mockReturnValue(of(graph())),
    saveGraph: vi.fn().mockReturnValue(of({ definition_revision: 1, base_graph_hash: 'a'.repeat(64) })),
    getWorkflowStatus: vi.fn().mockReturnValue(throwError(() => ({ status: 404 }))),
  };
  TestBed.configureTestingModule({
    imports: [CaseFlowOverviewComponent],
    providers: [
      provideRouter([]),
      { provide: OrganizationApiClient, useValue: organizations },
      { provide: VisualProcessApiService, useValue: api },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: HUB }] } },
      { provide: ProjectContextService, useValue: { selectedProjectId: () => projectId } },
    ],
  });
});

describe('both levels in one list', () => {
  it('offers an organisation and a team side by side, each marked with its level', () => {
    const fixture = mount();

    expect(cards(fixture)).toHaveLength(2);
    expect(text(fixture)).toContain('Mein Team');
    expect(text(fixture)).toContain('Produktorganisation');
    expect(text(fixture)).toContain('4 Teams · 2 Bereiche · Entwurf');
  });

  it('puts teams first, because that is the level people work at', () => {
    const fixture = mount();

    expect(cards(fixture)[0].nativeElement.textContent).toContain('Mein Team');
  });

  it('narrows the list to what was searched for, across both levels', () => {
    const fixture = mount();

    fixture.componentInstance['search'].set('produkt');
    fixture.detectChanges();

    expect(cards(fixture)).toHaveLength(1);
    expect(text(fixture)).toContain('Produktorganisation');
  });

  it('still shows the teams when the organisation catalog is unreachable', () => {
    organizations.listOrganizations.mockReturnValue(throwError(() => new Error('offline')));

    const fixture = mount();

    expect(cards(fixture)).toHaveLength(1);
    expect(text(fixture)).toContain('Mein Team');
    expect(text(fixture)).toContain('Die Organisationen konnten nicht geladen werden.');
  });

  it('still shows the organisations when the teams are unreachable', () => {
    api.listSavedGraphs.mockReturnValue(throwError(() => new Error('offline')));

    const fixture = mount();

    expect(cards(fixture)).toHaveLength(1);
    expect(text(fixture)).toContain('Produktorganisation');
    expect(text(fixture)).toContain('Die Teams konnten nicht geladen werden.');
  });

  it('says why organisations are absent when no project is chosen', () => {
    projectId = '';

    const fixture = mount();

    expect(organizations.listOrganizations).not.toHaveBeenCalled();
    expect(text(fixture)).toContain('Ohne gewähltes Projekt');
    expect(text(fixture)).toContain('Mein Team');
  });

  it('points at where a first team comes from when there is nothing yet', () => {
    api.listSavedGraphs.mockReturnValue(of([]));
    organizations.listOrganizations.mockReturnValue(of({ items: [], next_cursor: null }));

    const fixture = mount();

    expect(text(fixture)).toContain('Hier ist noch nichts.');
    expect(fixture.debugElement.query(By.css('a[href="/caseflow/team"]'))).not.toBeNull();
  });
});

describe('opening an organisation', () => {
  it('shows its structure with one row per part, indented as the Hub nested them', () => {
    const fixture = mount();

    cards(fixture)[1].nativeElement.click();
    fixture.detectChanges();

    const rows = fixture.debugElement.queryAll(By.css('.ov-row'));
    expect(rows).toHaveLength(2);
    expect(rows[0].nativeElement.textContent).toContain('Plattform');
    expect(rows[1].nativeElement.textContent).toContain('Alpha');
    expect(text(fixture)).toContain('1 Bereiche · 1 Teams');
  });

  it('offers the way onward to the editor that can change it', () => {
    const fixture = mount();

    cards(fixture)[1].nativeElement.click();
    fixture.detectChanges();

    expect(fixture.debugElement.query(By.css('a[href="/organizations"]'))).not.toBeNull();
  });

  it('says so when the structure cannot be read rather than showing an empty one', () => {
    organizations.topology.mockReturnValue(throwError(() => new Error('offline')));
    const fixture = mount();

    cards(fixture)[1].nativeElement.click();
    fixture.detectChanges();

    expect(text(fixture)).toContain('Die Struktur dieser Organisation konnte nicht gelesen werden.');
  });

  it('returns to both levels from an opened organisation', () => {
    const fixture = mount();
    cards(fixture)[1].nativeElement.click();
    fixture.detectChanges();

    fixture.debugElement.query(By.css('.ov-ghost')).nativeElement.click();
    fixture.detectChanges();

    expect(cards(fixture)).toHaveLength(2);
  });
});

describe('opening a team', () => {
  it('lands on the live map for that team', () => {
    const fixture = mount();

    cards(fixture)[0].nativeElement.click();
    fixture.detectChanges();

    expect(api.loadSavedGraph).toHaveBeenCalledWith('g-1');
    expect(fixture.debugElement.query(By.css('app-caseflow-agent-live-view'))).not.toBeNull();
  });

  it('stays on the overview when that team cannot be loaded', () => {
    api.loadSavedGraph.mockReturnValue(throwError(() => new Error('gone')));
    const fixture = mount();

    cards(fixture)[0].nativeElement.click();
    fixture.detectChanges();

    expect(text(fixture)).toContain('Dieses Team konnte nicht geladen werden.');
    expect(cards(fixture)).toHaveLength(2);
  });
});
