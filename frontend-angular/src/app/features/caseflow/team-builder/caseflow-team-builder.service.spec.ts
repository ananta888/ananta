/**
 * Which URLs this screen actually reads.
 *
 * The teams blueprint is registered at the root, not under /api like the
 * visual-process one — a first attempt guessed the prefix and got a 404 that
 * only showed up against a live hub. The paths are pinned here so the guess
 * cannot come back.
 */

import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { CaseFlowTeamBuilderService } from './caseflow-team-builder.service';

const HUB = 'http://hub.test:5000';

let service: CaseFlowTeamBuilderService;
let http: HttpTestingController;

beforeEach(() => {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: HUB }] } },
    ],
  });
  service = TestBed.inject(CaseFlowTeamBuilderService);
  http = TestBed.inject(HttpTestingController);
});

describe('reading the template catalog', () => {
  it('asks the hub where the teams blueprint actually is', () => {
    service.listTemplates().subscribe();

    http.expectOne(`${HUB}/teams/templates`).flush({ data: { schema: 'x', templates: [] } });
    http.verify();
  });

  it('unwraps the templates out of the standard response envelope', () => {
    let received: readonly unknown[] = [];
    service.listTemplates().subscribe(templates => (received = templates));

    http
      .expectOne(`${HUB}/teams/templates`)
      .flush({ status: 'success', data: { schema: 'x', templates: [{ template_id: 'team:1' }] } });

    expect(received).toHaveLength(1);
  });

  it('yields an empty list rather than undefined when the envelope carries nothing', () => {
    let received: readonly unknown[] | null = null;
    service.listTemplates().subscribe(templates => (received = templates));

    http.expectOne(`${HUB}/teams/templates`).flush({ status: 'success' });

    expect(received).toEqual([]);
  });
});

describe('reading the role catalog', () => {
  it('asks the hub for the roles people already edit under Teams', () => {
    service.listRoles().subscribe();

    http.expectOne(`${HUB}/teams/roles`).flush({ data: [] });
    http.verify();
  });

  it('drops rows that could only render as a blank option', () => {
    let received: readonly unknown[] = [];
    service.listRoles().subscribe(roles => (received = roles));

    http.expectOne(`${HUB}/teams/roles`).flush({
      data: [{ id: 'r-1', name: 'Tester' }, { id: '', name: 'Namenlos' }, { id: 'r-2', name: '' }],
    });

    expect(received).toEqual([{ id: 'r-1', name: 'Tester' }]);
  });
});
