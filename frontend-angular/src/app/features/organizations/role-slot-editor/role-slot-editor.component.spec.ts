import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, Subject } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import {
  OrganizationAssignmentCandidate,
  OrganizationRoleSlot,
} from '../models/organization-topology.models';
import { OrganizationApiClient } from '../services/organization-api.client';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { RoleSlotEditorComponent } from './role-slot-editor.component';

describe('RoleSlotEditorComponent project scope', () => {
  it('loads the initial organization and reloads even when only the project changes', () => {
    const projectId = signal('project-alpha');
    const state = {
      projectId,
      hubUrl: signal('https://hub.example'),
      selectedOrganizationId: signal<string | null>('shared-organization-id'),
      selectedOrganizationAdminGrant: signal(''),
      patchPreview: signal(null),
      mutating: signal(false),
      topologyPatchGrant: signal(null),
      previewOperations: vi.fn(),
      issuePreviewGrant: vi.fn(),
      applyPreview: vi.fn(),
    };
    const api = {
      roleSlots: vi.fn(() => of([roleSlot()])),
      assignmentCandidates: vi.fn(() => of([])),
    };
    TestBed.configureTestingModule({
      imports: [RoleSlotEditorComponent],
      providers: [
        { provide: OrganizationTopologyStateService, useValue: state },
        { provide: OrganizationApiClient, useValue: api },
      ],
    });

    const fixture = TestBed.createComponent(RoleSlotEditorComponent);
    fixture.detectChanges();
    expect(api.roleSlots).toHaveBeenCalledTimes(1);
    expect(api.roleSlots).toHaveBeenLastCalledWith(
      'https://hub.example',
      'shared-organization-id',
    );

    fixture.componentInstance.adminGrant = 'project-alpha-grant';
    fixture.componentInstance.confirmed = true;
    projectId.set('project-beta');
    TestBed.flushEffects();

    expect(api.roleSlots).toHaveBeenCalledTimes(2);
    expect(fixture.componentInstance.adminGrant).toBe('');
    expect(fixture.componentInstance.confirmed).toBe(false);
  });

  it('ignores candidates returned for a previously selected slot', () => {
    const state = {
      projectId: signal('project-alpha'),
      hubUrl: signal('https://hub.example'),
      selectedOrganizationId: signal<string | null>('organization-alpha'),
      selectedOrganizationAdminGrant: signal(''),
      patchPreview: signal(null),
      mutating: signal(false),
      topologyPatchGrant: signal(null),
      previewOperations: vi.fn(),
      issuePreviewGrant: vi.fn(),
      applyPreview: vi.fn(),
    };
    const alphaCandidates = new Subject<OrganizationAssignmentCandidate[]>();
    const betaCandidates = new Subject<OrganizationAssignmentCandidate[]>();
    const alphaSlot = roleSlot('role-slot-alpha');
    const betaSlot = roleSlot('role-slot-beta');
    const api = {
      roleSlots: vi.fn(() => of([alphaSlot, betaSlot])),
      assignmentCandidates: vi.fn((_hubUrl: string, _organizationId: string, slotId: string) => (
        slotId === alphaSlot.id ? alphaCandidates : betaCandidates
      )),
    };
    TestBed.configureTestingModule({
      imports: [RoleSlotEditorComponent],
      providers: [
        { provide: OrganizationTopologyStateService, useValue: state },
        { provide: OrganizationApiClient, useValue: api },
      ],
    });

    const fixture = TestBed.createComponent(RoleSlotEditorComponent);
    fixture.detectChanges();
    fixture.componentInstance.selectSlot(betaSlot);
    betaCandidates.next([candidate('agent-beta')]);
    alphaCandidates.next([candidate('agent-alpha')]);

    expect(fixture.componentInstance.selectedSlot()?.id).toBe(betaSlot.id);
    expect(fixture.componentInstance.candidates().map(item => item.agent_id)).toEqual(['agent-beta']);
  });
});

function roleSlot(id = 'role-slot-1'): OrganizationRoleSlot {
  return {
    id,
    stable_key: id,
    role_template_key: 'developer',
    role_template_version: '1.0.0',
    label: 'Developer',
    min_count: 1,
    default_count: 1,
    max_count: 3,
    required_capabilities: ['code'],
    risk_level: 'medium',
    independent_verification_required: false,
    assignments: [],
  };
}

function candidate(agentId: string): OrganizationAssignmentCandidate {
  return {
    agent_id: agentId,
    label: agentId,
    compatible: true,
    capacity_used: 0,
    capacity_limit: 1,
    affected_teams: [],
    reasons: [],
  };
}
