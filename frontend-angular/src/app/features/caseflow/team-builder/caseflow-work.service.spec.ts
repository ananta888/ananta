/**
 * Where the work is read from, and how a level is narrowed.
 *
 * The task blueprint is registered at the root, not under /api — the same
 * trap the team catalog fell into, so the paths are pinned. The other thing
 * pinned is the narrowing: the Hub has no team filter, and a team must never
 * be shown the whole organisation's work under its own name.
 */

import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import type { TaskView, TraceEntry } from './caseflow-work.models';
import { CaseFlowWorkService } from './caseflow-work.service';

const HUB = 'http://hub.test:5000';

let service: CaseFlowWorkService;
let http: HttpTestingController;

function task(overrides: Record<string, unknown> = {}) {
  return { id: 't-1', title: 'Etwas', status: 'in_progress', team_id: 'team-1', ...overrides };
}

beforeEach(() => {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: HUB }] } },
    ],
  });
  service = TestBed.inject(CaseFlowWorkService);
  http = TestBed.inject(HttpTestingController);
});

describe('reading the tasks', () => {
  it('asks the hub where the task blueprint actually is', () => {
    service.tasks({ level: 'organization' }).subscribe();

    const request = http.expectOne(item => item.url === `${HUB}/tasks`);
    expect(request.request.params.get('limit')).toBe('200');
    expect(request.request.params.get('agent')).toBeNull();
    request.flush({ data: [] });
    http.verify();
  });

  it('lets the Hub filter by agent, because it can', () => {
    service.tasks({ level: 'agent', agent: 'http://agent-a' }).subscribe();

    const request = http.expectOne(item => item.url === `${HUB}/tasks`);
    expect(request.request.params.get('agent')).toBe('http://agent-a');
    request.flush({ data: [] });
  });

  it('narrows a team here, because the Hub has no team filter', () => {
    let received: readonly TaskView[] = [];
    service.tasks({ level: 'team', team_id: 'team-1' }).subscribe(tasks => (received = tasks));

    http.expectOne(item => item.url === `${HUB}/tasks`).flush({
      data: [task(), task({ id: 't-2', team_id: 'team-2' })],
    });

    expect(received.map(item => item.id)).toEqual(['t-1']);
  });

  it('does not narrow an organisation scope by a team it was not asked about', () => {
    let received: readonly TaskView[] = [];
    service.tasks({ level: 'organization' }).subscribe(tasks => (received = tasks));

    http.expectOne(item => item.url === `${HUB}/tasks`).flush({
      data: [task(), task({ id: 't-2', team_id: 'team-2' })],
    });

    expect(received).toHaveLength(2);
  });

  it('accepts a bare list as readily as the standard envelope', () => {
    let received: readonly TaskView[] = [];
    service.tasks({ level: 'organization' }).subscribe(tasks => (received = tasks));

    http.expectOne(item => item.url === `${HUB}/tasks`).flush([task()]);

    expect(received).toHaveLength(1);
  });
});

describe('reading the trace', () => {
  it('asks the timeline and lets it filter by the level it was given', () => {
    service.trace({ level: 'team', team_id: 'team-1' }).subscribe();

    const request = http.expectOne(item => item.url === `${HUB}/tasks/timeline`);
    expect(request.request.params.get('team_id')).toBe('team-1');
    expect(request.request.params.get('limit')).toBe('200');
    request.flush({ data: { events: [] } });
    http.verify();
  });

  it('sends no filter at all for the organisation as a whole', () => {
    service.trace({ level: 'organization' }).subscribe();

    const request = http.expectOne(item => item.url === `${HUB}/tasks/timeline`);
    expect(request.request.params.get('team_id')).toBeNull();
    expect(request.request.params.get('agent')).toBeNull();
    request.flush({ data: { events: [] } });
  });

  it('reads the events out of the envelope the Hub wraps them in', () => {
    let received: readonly TraceEntry[] = [];
    service.trace({ level: 'organization' }).subscribe(entries => (received = entries));

    http.expectOne(item => item.url === `${HUB}/tasks/timeline`).flush({
      data: { events: [{ event_type: 'task_created', task_id: 't-1', actor: 'planner' }] },
    });

    expect(received).toHaveLength(1);
    expect(received[0].creating).toBe(true);
  });
});
