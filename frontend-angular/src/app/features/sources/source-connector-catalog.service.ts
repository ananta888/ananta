import { Injectable, inject } from '@angular/core';
import { map } from 'rxjs';
import type { Observable } from 'rxjs';

import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';

export type SourceConnectorKind =
  | 'direct_text'
  | 'open_notebook'
  | 'registered_workspace'
  | 'registered_remote';

export interface SourceConnectorCapability {
  readonly kind: SourceConnectorKind;
  readonly label: string;
  readonly description: string;
  readonly available: boolean;
  readonly persistable: boolean;
  readonly reason?: string;
}

export interface SourceWorkspaceOption {
  readonly workspaceId: string;
  readonly label: string;
  readonly enabled: boolean;
  readonly readOnly: boolean;
}

export interface SourceRemoteOption {
  readonly remoteId: string;
  readonly label: string;
  readonly kind: 'git' | 'github';
  readonly repository: string | null;
  readonly state: string;
  readonly active: boolean;
}

export interface SourceIndexProfileOption {
  readonly profileId: string;
  readonly label: string;
  readonly description: string;
  readonly isDefault: boolean;
}

const CAPABILITIES: readonly SourceConnectorCapability[] = [
  {
    kind: 'direct_text',
    label: 'Direkttext',
    description: 'Text oder Markdown ueber die serverseitige Content-Admission aufnehmen.',
    available: true,
    persistable: true,
  },
  {
    kind: 'open_notebook',
    label: 'Notebook',
    description: 'Ein kanonisches Notebook ueber die serverseitige Content-Admission aufnehmen.',
    available: true,
    persistable: true,
  },
  {
    kind: 'registered_workspace',
    label: 'Registrierter Workspace',
    description: 'Eine serverseitig registrierte Workspace-ID anbinden.',
    available: true,
    persistable: true,
  },
  {
    kind: 'registered_remote',
    label: 'Registriertes Remote',
    description: 'Eine serverseitig registrierte Remote-ID anbinden.',
    available: true,
    persistable: true,
  },
];

@Injectable({ providedIn: 'root' })
export class SourceConnectorCatalogService {
  private readonly api = inject(SourceControlV1GovernanceApiClient);

  readonly capabilities = CAPABILITIES;

  loadWorkspaces(projectId: string): Observable<readonly SourceWorkspaceOption[]> {
    return this.api.listWorkspaces(projectId).pipe(
      map((page) =>
        page.items.map((workspace) => ({
          workspaceId: workspace.workspace_id,
          label: workspace.workspace_id,
          enabled: workspace.enabled,
          readOnly: workspace.read_only,
        })),
      ),
    );
  }

  loadRemotes(projectId: string): Observable<readonly SourceRemoteOption[]> {
    return this.api.listRegisteredRemotes(projectId).pipe(
      map((page) =>
        page.items.map((remote) => ({
          remoteId: remote.remote_id,
          label: remote.repository ?? remote.remote_id,
          kind: remote.kind,
          repository: remote.repository,
          state: remote.state,
          active: remote.state === 'active',
        })),
      ),
    );
  }

  loadIndexProfiles(projectId: string): Observable<readonly SourceIndexProfileOption[]> {
    return this.api.listIndexProfiles(projectId).pipe(
      map((page) =>
        page.items.map((profile) => ({
          profileId: profile.profile_id,
          label: profile.label,
          description: profile.description,
          isDefault: profile.is_default,
        })),
      ),
    );
  }
}
