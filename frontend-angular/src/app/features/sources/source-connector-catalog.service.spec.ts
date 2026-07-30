import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { SourceConnectorCatalogService } from './source-connector-catalog.service';

describe('SourceConnectorCatalogService', () => {
  const api = jasmine.createSpyObj<SourceControlV1GovernanceApiClient>(
    'SourceControlV1GovernanceApiClient',
    ['listWorkspaces', 'listRegisteredRemotes', 'listIndexProfiles'],
  );

  beforeEach(() => {
    api.listWorkspaces.calls.reset();
    api.listRegisteredRemotes.calls.reset();
    api.listIndexProfiles.calls.reset();
    TestBed.configureTestingModule({
      providers: [
        SourceConnectorCatalogService,
        { provide: SourceControlV1GovernanceApiClient, useValue: api },
      ],
    });
  });

  it('exposes only server-authoritative content and registered connection sources', () => {
    const service = TestBed.inject(SourceConnectorCatalogService);

    expect(service.capabilities.find((item) => item.kind === 'direct_text')?.persistable).toBeTrue();
    expect(service.capabilities.find((item) => item.kind === 'open_notebook')?.persistable).toBeTrue();
    expect(
      service.capabilities.find((item) => item.kind === 'registered_workspace')?.persistable,
    ).toBeTrue();
    expect(
      service.capabilities.find((item) => item.kind === 'registered_remote')?.persistable,
    ).toBeTrue();
  });

  it('maps the server-authoritative workspace catalog without local allow lists', (done) => {
    api.listWorkspaces.and.returnValue(
      of({
        items: [
          {
            workspace_id: 'workspace-primary',
            enabled: true,
            read_only: true,
            capabilities: [],
          },
        ],
        next_cursor: null,
        capabilities: [],
      }),
    );

    TestBed.inject(SourceConnectorCatalogService)
      .loadWorkspaces('project-alpha')
      .subscribe((items) => {
        expect(api.listWorkspaces).toHaveBeenCalledOnceWith('project-alpha');
        expect(items).toEqual([
          {
            workspaceId: 'workspace-primary',
            label: 'workspace-primary',
            enabled: true,
            readOnly: true,
          },
        ]);
        done();
      });
  });

  it('maps registered remotes and index profiles from v1 catalogs', (done) => {
    api.listRegisteredRemotes.and.returnValue(
      of({
        items: [
          {
            remote_id: 'remote-primary',
            kind: 'git',
            repository: 'team/repository',
            state: 'ready',
            capabilities: [],
          },
        ],
        next_cursor: null,
        capabilities: [],
      }),
    );
    api.listIndexProfiles.and.returnValue(
      of({
        items: [
          {
            profile_id: 'profile-default',
            label: 'Default',
            description: 'Default profile',
            is_default: true,
            capabilities: [],
          },
        ],
        next_cursor: null,
        capabilities: [],
      }),
    );

    const service = TestBed.inject(SourceConnectorCatalogService);
    service.loadRemotes('project-alpha').subscribe((items) => {
      expect(items[0]).toEqual({
        remoteId: 'remote-primary',
        label: 'team/repository',
        kind: 'git',
        repository: 'team/repository',
        state: 'ready',
      });
      service.loadIndexProfiles('project-alpha').subscribe((profiles) => {
        expect(profiles[0]?.profileId).toBe('profile-default');
        expect(api.listIndexProfiles).toHaveBeenCalledOnceWith('project-alpha');
        done();
      });
    });
  });
});
