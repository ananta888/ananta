/**
 * The screen a person assembles a team on.
 *
 * What is pinned here is the path through it — pick, name, save, watch — and
 * the two ways it is allowed to degrade: a source that fails must cost only
 * its own list, and a draft a person could not tell apart while it runs must
 * not be savable.
 */

import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { of, throwError } from 'rxjs';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { VisualProcessApiService, type VpGraph } from '../../visual-process/visual-process-api.service';
import { CaseFlowTeamBuilderComponent } from './caseflow-team-builder.component';
import type { TeamTemplate } from './caseflow-team-builder.models';
import { CaseFlowTeamBuilderService } from './caseflow-team-builder.service';

beforeAll(async () => {
  await ɵresolveComponentResources(async resource => {
    const name = resource.split('/').at(-1) || resource;
    const candidates = [
      new URL(resource, import.meta.url),
      resolve(process.cwd(), 'src/app/features/caseflow/team-builder', name),
      // The canvas renders for real here — it takes no dependencies, so the
      // graph this screen builds is proven to be one it can draw.
      resolve(process.cwd(), 'src/app/features/caseflow/agent-canvas', name),
      resolve(process.cwd(), 'src/app/features/visual-process', name),
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

const TEAM: TeamTemplate = {
  template_id: 'team:tt-1',
  kind: 'team',
  source_id: 'tt-1',
  display_name: 'Scrum',
  description: 'Ein Standardteam',
  roles: [
    { role_id: 'r-1', display_name: 'Product Owner' },
    { role_id: 'r-2', display_name: 'Scrum Master' },
    { role_id: 'r-3', display_name: 'Developer' },
  ],
  aliases: [],
  agent_count: 3,
};

const PROCESS: TeamTemplate = {
  template_id: 'process:preset-tdd-loop',
  kind: 'process',
  source_id: 'preset-tdd-loop',
  display_name: 'Erst Test, dann Code',
  description: 'Ein Agent schreibt einen Test, ein zweiter baut.',
  roles: [],
  aliases: ['TDD'],
  agent_count: 0,
};

function savedGraph(): VpGraph {
  return {
    id: 'g-1',
    name: 'Bestehend',
    description: '',
    version: '1.0.0',
    tags: [],
    steps: [
      {
        id: 'agent-1',
        label: 'Mara',
        kind: 'coding',
        role: 'Developer',
        io: { inputs: [], outputs: [] },
        position: { x: 120, y: 160 },
        policy_hints: [],
        gate: false,
      },
    ],
    edges: [],
  } as unknown as VpGraph;
}

let builder: { listTemplates: ReturnType<typeof vi.fn>; listRoles: ReturnType<typeof vi.fn> };
let api: {
  saveGraph: ReturnType<typeof vi.fn>;
  listSavedGraphs: ReturnType<typeof vi.fn>;
  loadSavedGraph: ReturnType<typeof vi.fn>;
};

function mount() {
  const fixture = TestBed.createComponent(CaseFlowTeamBuilderComponent);
  fixture.detectChanges();
  return fixture;
}

function text(fixture: ReturnType<typeof mount>): string {
  return fixture.nativeElement.textContent as string;
}

function cards(fixture: ReturnType<typeof mount>) {
  return fixture.debugElement.queryAll(By.css('.tb-card'));
}

beforeEach(() => {
  builder = {
    listTemplates: vi.fn().mockReturnValue(of([TEAM, PROCESS])),
    listRoles: vi.fn().mockReturnValue(of([{ id: 'r-4', name: 'Tester' }])),
  };
  api = {
    saveGraph: vi.fn().mockReturnValue(of({ saved: true })),
    listSavedGraphs: vi.fn().mockReturnValue(of([])),
    loadSavedGraph: vi.fn().mockReturnValue(of(savedGraph())),
  };
  TestBed.configureTestingModule({
    imports: [CaseFlowTeamBuilderComponent],
    providers: [
      provideRouter([]),
      { provide: CaseFlowTeamBuilderService, useValue: builder },
      { provide: VisualProcessApiService, useValue: api },
    ],
  });
});

describe('the gallery', () => {
  it('offers both kinds of template, each labelled by which question it answers', () => {
    const fixture = mount();

    expect(text(fixture)).toContain('Scrum');
    expect(text(fixture)).toContain('Erst Test, dann Code');
    expect(cards(fixture)).toHaveLength(2);
  });

  it('finds a template by an alias rather than only by its shown name', () => {
    const fixture = mount();

    fixture.componentInstance['search'].set('TDD');
    fixture.detectChanges();

    expect(cards(fixture)).toHaveLength(1);
    expect(text(fixture)).toContain('Erst Test, dann Code');
  });

  it('finds a template by a role it brings', () => {
    const fixture = mount();

    fixture.componentInstance['search'].set('scrum master');
    fixture.detectChanges();

    expect(cards(fixture)).toHaveLength(1);
    expect(text(fixture)).toContain('Scrum');
  });

  it('lists teams a person already saved alongside the templates', () => {
    api.listSavedGraphs.mockReturnValue(of([{ id: 'g-1', name: 'Bestehend', description: '', tags: [] }]));

    const fixture = mount();

    expect(text(fixture)).toContain('Deine Teams');
    expect(text(fixture)).toContain('Bestehend');
  });

  it('still offers the templates when the saved teams cannot be read', () => {
    api.listSavedGraphs.mockReturnValue(throwError(() => new Error('offline')));

    const fixture = mount();

    expect(cards(fixture)).toHaveLength(2);
    expect(text(fixture)).not.toContain('Deine Teams');
  });

  it('says so plainly when the templates cannot be read', () => {
    builder.listTemplates.mockReturnValue(throwError(() => new Error('offline')));

    const fixture = mount();

    expect(text(fixture)).toContain('Die Vorlagen konnten nicht geladen werden.');
  });
});

describe('naming the team', () => {
  it('starts every role as an agent named after it', async () => {
    const fixture = mount();

    cards(fixture)[0].nativeElement.click();
    fixture.detectChanges();
    // ngModel writes the input value on a microtask, not during change detection.
    await fixture.whenStable();

    const names = fixture.debugElement.queryAll(By.css('.tb-agent-name')).map(input => input.nativeElement.value);
    expect(names).toEqual(['Product Owner', 'Scrum Master', 'Developer']);
  });

  it('refuses to save two agents a person could not tell apart', () => {
    const fixture = mount();
    cards(fixture)[0].nativeElement.click();
    fixture.detectChanges();

    fixture.componentInstance['renameAgent']('agent-2', 'Product Owner');
    fixture.detectChanges();

    expect(text(fixture)).toContain('ist doppelt vergeben');
    expect(fixture.debugElement.query(By.css('.tb-primary')).nativeElement.disabled).toBe(true);
    expect(api.saveGraph).not.toHaveBeenCalled();
  });

  it('refuses to save a process template until it has an agent', () => {
    const fixture = mount();

    cards(fixture)[1].nativeElement.click();
    fixture.detectChanges();

    expect(text(fixture)).toContain('Mindestens ein Agent wird gebraucht.');
    expect(fixture.debugElement.query(By.css('.tb-primary')).nativeElement.disabled).toBe(true);
  });

  it('adds an agent from the live role catalog', () => {
    const fixture = mount();
    cards(fixture)[1].nativeElement.click();
    fixture.detectChanges();

    fixture.componentInstance['roleToAdd'].set('r-4');
    fixture.componentInstance['addAgent']();
    fixture.detectChanges();

    expect(text(fixture)).toContain('Tester');
    expect(fixture.debugElement.query(By.css('.tb-primary')).nativeElement.disabled).toBe(false);
  });
});

describe('saving and watching', () => {
  it('saves the named agents and shows the team on the canvas', () => {
    const fixture = mount();
    cards(fixture)[0].nativeElement.click();
    fixture.detectChanges();
    fixture.componentInstance['renameAgent']('agent-1', 'Mara');
    fixture.detectChanges();

    fixture.debugElement.query(By.css('.tb-primary')).nativeElement.click();
    fixture.detectChanges();

    const saved = api.saveGraph.mock.calls[0][0] as VpGraph;
    expect(saved.name).toBe('Scrum');
    expect(saved.steps.map(step => step.label)).toEqual(['Mara', 'Scrum Master', 'Developer']);
    expect(fixture.debugElement.query(By.css('app-caseflow-agent-canvas'))).not.toBeNull();
  });

  it('keeps the draft when saving fails so nothing typed is lost', () => {
    api.saveGraph.mockReturnValue(throwError(() => new Error('conflict')));
    const fixture = mount();
    cards(fixture)[0].nativeElement.click();
    fixture.detectChanges();

    fixture.debugElement.query(By.css('.tb-primary')).nativeElement.click();
    fixture.detectChanges();

    expect(text(fixture)).toContain('Das Team konnte nicht angelegt werden.');
    expect(fixture.debugElement.queryAll(By.css('.tb-agent-name'))).toHaveLength(3);
  });

  it('opens a saved team on the same canvas', () => {
    api.listSavedGraphs.mockReturnValue(of([{ id: 'g-1', name: 'Bestehend', description: '', tags: [] }]));
    const fixture = mount();

    fixture.debugElement.query(By.css('.tb-card--saved')).nativeElement.click();
    fixture.detectChanges();

    expect(api.loadSavedGraph).toHaveBeenCalledWith('g-1');
    expect(fixture.debugElement.query(By.css('app-caseflow-agent-canvas'))).not.toBeNull();
  });

  it('points onward to the editors that configure the same team more finely', () => {
    const fixture = mount();
    cards(fixture)[0].nativeElement.click();
    fixture.detectChanges();
    fixture.debugElement.query(By.css('.tb-primary')).nativeElement.click();
    fixture.detectChanges();

    const targets = fixture.debugElement
      .queryAll(By.css('.tb-links a'))
      .map(link => link.nativeElement.getAttribute('href'));
    expect(targets).toEqual(['/process-designer', '/caseflow/studio', '/codehug/internals']);
  });
});
