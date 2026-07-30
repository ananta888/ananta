import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';

import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { SourceConnectorCatalogService } from './source-connector-catalog.service';

describe('SourceConnectorCatalogService', () => {
  const listWorkspaces = vi.fn();
  const listRegisteredRemotes = vi.fn();
  const listIndexProfiles = vi.fn();
  const api = { listWorkspaces, listRegisteredRemotes, listIndexProfiles };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        SourceConnectorCatalogService,
        { provide: SourceControlV1GovernanceApiClient, useValue: api },
      ],
    });
  });

  it('exposes only server-authoritative content and registered connection sources', () => {
    const capabilities = TestBed.inject(SourceConnectorCatalogService).capabilities;

    expect(capabilities.find((item) => item.kind === 'direct_text')?.persistable).toBeTruthy();
    expect(capabilities.find((item) => item.kind === 'open_notebook')?.persistable).toBeTruthy();
    expect(capabilities.find((item) => item.kind === 'registered_workspace')?.persistable).toBeTruthy();
    expect(capabilities.find((item) => item.kind === 'registered_remote')?.persistable).toBeTruthy();
  });

  it('maps the server-authoritative workspace catalog without local allow lists', () => {
    listWorkspaces.mockReturnValue(of({
      items: [{
        workspace_id: 'workspace-primary',
        enabled: true,
        read_only: true,
        capabilities: {},
      }],
      next_cursor: null,
      capabilities: {},
    }));

    let actual: unknown;
    TestBed.inject(SourceConnectorCatalogService)
      .loadWorkspaces('project-alpha')
      .subscribe((items) => actual = items);

    expect(listWorkspaces).toHaveBeenCalledWith('project-alpha');
    expect(actual).toEqual([{
      workspaceId: 'workspace-primary',
      label: 'workspace-primary',
      enabled: true,
      readOnly: true,
    }]);
  });

  it('maps registered remotes and index profiles from v1 catalogs', () => {
    listRegisteredRemotes.mockReturnValue(of({
      items: [{
        remote_id: 'remote-primary',
        kind: 'git',
        repository: 'team/repository',
        state: 'ready',
        capabilities: {},
      }],
      next_cursor: null,
      capabilities: {},
    }));
    listIndexProfiles.mockReturnValue(of({
      items: [{
        profile_id: 'profile-default',
        label: 'Default',
        description: 'Default profile',
        is_default: true,
        capabilities: {},
      }],
      next_cursor: null,
      capabilities: {},
    }));

    const service = TestBed.inject(SourceConnectorCatalogService);
    let remotes: unknown;
    let profiles: unknown;
    service.loadRemotes('project-alpha').subscribe((items) => remotes = items);
    service.loadIndexProfiles('project-alpha').subscribe((items) => profiles = items);

    expect(remotes).toEqual([{
      remoteId: 'remote-primary',
      label: 'team/repository',
      kind: 'git',
      repository: 'team/repository',
      state: 'ready',
    }]);
    expect(profiles).toEqual([{
      profileId: 'profile-default',
      label: 'Default',
      description: 'Default profile',
      isDefault: true,
    }]);
    expect(listIndexProfiles).toHaveBeenCalledWith('project-alpha');
  });
});
