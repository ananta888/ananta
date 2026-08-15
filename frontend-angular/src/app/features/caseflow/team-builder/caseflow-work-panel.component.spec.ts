/**
 * The same three questions, at whatever level they were asked.
 *
 * What is pinned is that the panel re-reads when the level changes, that a
 * level with no identity to filter by shows nothing rather than everything,
 * and that a failing trace does not take the tasks down with it.
 */

import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { of, throwError } from 'rxjs';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { toTaskViews, toTraceEntries, type WorkScope } from './caseflow-work.models';
import { CaseFlowWorkPanelComponent } from './caseflow-work-panel.component';
import { CaseFlowWorkService } from './caseflow-work.service';

beforeAll(async () => {
  await ɵresolveComponentResources(async resource => {
    const name = resource.split('/').at(-1) || resource;
    for (const candidate of [
      new URL(resource, import.meta.url),
      resolve(process.cwd(), 'src/app/features/caseflow/team-builder', name),
    ]) {
      try {
        return await readFile(candidate, 'utf8');
      } catch {
        // Angular reports relative URLs only; try the owning directory.
      }
    }
    throw new Error(`Component resource not found: ${resource}`);
  });
});

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: 't-1',
    title: 'Index aktualisieren',
    status: 'in_progress',
    team_id: 'team-1',
    assigned_agent_url: 'http://agent-a',
    updated_at: 10,
    created_at: 5,
    ...overrides,
  };
}

let work: { tasks: ReturnType<typeof vi.fn>; trace: ReturnType<typeof vi.fn> };

function mount(scope: WorkScope = { level: 'organization' }) {
  const fixture = TestBed.createComponent(CaseFlowWorkPanelComponent);
  fixture.componentRef.setInput('scope', scope);
  fixture.detectChanges();
  return fixture;
}

function text(fixture: ReturnType<typeof mount>): string {
  return fixture.nativeElement.textContent as string;
}

function tab(fixture: ReturnType<typeof mount>, index: number) {
  return fixture.debugElement.queryAll(By.css('.work-tabs button'))[index].nativeElement as HTMLButtonElement;
}

beforeEach(() => {
  work = {
    tasks: vi.fn().mockReturnValue(of(toTaskViews([task(), task({ id: 't-2', status: 'created', title: 'Neu' })]))),
    trace: vi.fn().mockReturnValue(
      of(toTraceEntries([{ event_type: 'task_created', task_id: 't-2', actor: 'planner', details: { title: 'Neu' } }])),
    ),
  };
  TestBed.configureTestingModule({
    imports: [CaseFlowWorkPanelComponent],
    providers: [{ provide: CaseFlowWorkService, useValue: work }],
  });
});

describe('who is busy', () => {
  it('opens on who is working, with a count per question', () => {
    const fixture = mount();

    expect(text(fixture)).toContain('Arbeitet gerade (1)');
    expect(text(fixture)).toContain('Entsteht gerade (1)');
    expect(text(fixture)).toContain('Verlauf (1)');
  });

  it('names the agent and what it holds', () => {
    const fixture = mount();

    expect(text(fixture)).toContain('http://agent-a');
    expect(text(fixture)).toContain('Index aktualisieren');
    expect(text(fixture)).toContain('1 aktiv');
  });

  it('says plainly when nobody is working rather than showing an empty box', () => {
    work.tasks.mockReturnValue(of(toTaskViews([task({ status: 'completed' })])));

    const fixture = mount();

    expect(text(fixture)).toContain('Hier arbeitet gerade niemand.');
  });
});

describe('what is being created', () => {
  it('lists the tasks coming into being under their own tab', () => {
    const fixture = mount();

    tab(fixture, 1).click();
    fixture.detectChanges();

    expect(text(fixture)).toContain('Neu');
  });

  it('keeps blocked work visible under its own heading', () => {
    work.tasks.mockReturnValue(of(toTaskViews([task({ id: 't-3', status: 'blocked', title: 'Steht' })])));
    const fixture = mount();

    tab(fixture, 1).click();
    fixture.detectChanges();

    expect(text(fixture)).toContain('Wartet (1)');
    expect(text(fixture)).toContain('Steht');
  });

  it('says so when nothing is appearing', () => {
    work.tasks.mockReturnValue(of(toTaskViews([task()])));
    const fixture = mount();

    tab(fixture, 1).click();
    fixture.detectChanges();

    expect(text(fixture)).toContain('Gerade entsteht nichts Neues.');
  });
});

describe('the trace', () => {
  it('shows who did what, marking what brought a task into being', () => {
    const fixture = mount();

    tab(fixture, 2).click();
    fixture.detectChanges();

    expect(text(fixture)).toContain('planner');
    expect(fixture.debugElement.queryAll(By.css('.work-trace-line--creating'))).toHaveLength(1);
  });

  it('keeps the tasks when the trace cannot be read', () => {
    work.trace.mockReturnValue(throwError(() => new Error('offline')));

    const fixture = mount();

    expect(text(fixture)).toContain('Index aktualisieren');
    expect(text(fixture)).toContain('Verlauf (0)');
  });

  it('reports tasks that cannot be read instead of showing an idle level', () => {
    work.tasks.mockReturnValue(throwError(() => new Error('offline')));

    const fixture = mount();

    expect(text(fixture)).toContain('Die Aufgaben dieser Ebene konnten nicht gelesen werden.');
  });
});

describe('changing level', () => {
  it('re-reads for the level it was moved to', () => {
    const fixture = mount({ level: 'organization' });

    fixture.componentRef.setInput('scope', { level: 'team', team_id: 'team-1' });
    fixture.detectChanges();

    expect(work.tasks).toHaveBeenLastCalledWith({ level: 'team', team_id: 'team-1' });
    expect(work.trace).toHaveBeenLastCalledWith({ level: 'team', team_id: 'team-1' });
  });

  it('shows nothing for a level with no identity to filter by', () => {
    const fixture = mount({ level: 'team' });

    expect(work.tasks).not.toHaveBeenCalled();
    expect(text(fixture)).toContain('keine eigene Kennung');
  });

  it('reads again when asked to refresh', () => {
    const fixture = mount();

    fixture.debugElement.query(By.css('.work-ghost')).nativeElement.click();

    expect(work.tasks).toHaveBeenCalledTimes(2);
  });
});
