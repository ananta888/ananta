import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';

import { ProjectContextService } from '../../services/project-context.service';
import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';
import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { SourceConnectorCatalogService } from './source-connector-catalog.service';
import { SourceDetailFacade } from './source-detail.facade';
import { SourceIndexJourneyComponent } from './source-index-journey.component';

describe('SourceIndexJourneyComponent', () => {
  const selectedProjectId = signal<string | null>('project-alpha');
  const detail = {
    runs: signal<readonly never[]>([]),
    loading: signal(false),
    mutationLoading: signal(false),
    indexProfiles: signal<readonly { profileId: string }[]>([]),
    sourceError: signal(null),
    lifecycleMessage: signal(''),
    can: vi.fn().mockReturnValue(false),
    load: vi.fn(),
    refresh: vi.fn(),
    scan: vi.fn(),
    startIndex: vi.fn(),
    activateIndex: vi.fn(),
    rollbackIndex: vi.fn(),
  };
  const api = {
    listConnections: vi.fn(),
    validateConnection: vi.fn(),
    createConnection: vi.fn(),
  };
  const governance = { gitAuthorizationHealth: vi.fn() };
  const catalog = {
    loadWorkspaces: vi.fn(),
    loadRemotes: vi.fn(),
    loadIndexProfiles: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    selectedProjectId.set('project-alpha');
    detail.runs.set([]);
    detail.indexProfiles.set([]);
    detail.can.mockReturnValue(false);
    api.listConnections.mockReturnValue(of({ items: [], next_cursor: null }));
    governance.gitAuthorizationHealth.mockReturnValue(of({
      status: 'unavailable',
      reason_code: 'provider_not_configured',
      connector_ready: { github_repository: false, generic_git: false },
    }));
    catalog.loadWorkspaces.mockReturnValue(of([]));
    catalog.loadRemotes.mockReturnValue(of([]));
    catalog.loadIndexProfiles.mockReturnValue(of([]));
    TestBed.configureTestingModule({
      providers: [
        { provide: ProjectContextService, useValue: { selectedProjectId } },
        { provide: SourceControlV1ApiClient, useValue: api },
        { provide: SourceControlV1GovernanceApiClient, useValue: governance },
        { provide: SourceConnectorCatalogService, useValue: catalog },
        { provide: SourceDetailFacade, useValue: detail },
      ],
    });
  });

  it('keeps public Git and workspace onboarding available while gating an unavailable private provider', () => {
    const journey = TestBed.runInInjectionContext(() => new SourceIndexJourneyComponent());
    TestBed.flushEffects();

    expect(journey.providerAccess()).toEqual({
      publicGit: true,
      workspace: true,
      privateGit: false,
    });
    expect(journey.stage()).toBe('choose');
  });

  it('only selects a Hub-listed connection from the active project', () => {
    const connectionId = `conn_${'1'.repeat(64)}`;
    api.listConnections.mockReturnValue(of({
      items: [{
        connection_id: connectionId,
        connection: {
          project_id: 'project-alpha',
          display_name: 'Primary source',
          connector_type: 'git',
        },
      }],
      next_cursor: null,
    }));
    const journey = TestBed.runInInjectionContext(() => new SourceIndexJourneyComponent());
    TestBed.flushEffects();

    journey.chooseExisting(connectionId);

    expect(journey.selectedConnectionId()).toBe(connectionId);
    expect(journey.stage()).toBe('scan');
    expect(detail.load).toHaveBeenCalledWith(connectionId);
    journey.chooseExisting('browser-invented-connection');
    expect(detail.load).toHaveBeenCalledTimes(1);
  });

  it('starts indexing only after the detail facade has the same Hub profile', () => {
    const connectionId = `conn_${'2'.repeat(64)}`;
    const profile = {
      profileId: 'profile-deep-code',
      label: 'Deep Code',
      description: '',
      isDefault: true,
    };
    api.listConnections.mockReturnValue(of({
      items: [{
        connection_id: connectionId,
        connection: {
          project_id: 'project-alpha',
          display_name: 'Primary source',
          connector_type: 'git',
        },
      }],
      next_cursor: null,
    }));
    catalog.loadIndexProfiles.mockReturnValue(of([profile]));
    detail.can.mockImplementation((action: string) => action === 'index');
    const journey = TestBed.runInInjectionContext(() => new SourceIndexJourneyComponent());
    TestBed.flushEffects();
    journey.chooseExisting(connectionId);

    expect(journey.profileId()).toBe(profile.profileId);
    expect(journey.canStartIndex()).toBe(false);

    detail.indexProfiles.set([profile]);

    expect(journey.canStartIndex()).toBe(true);
  });
});
