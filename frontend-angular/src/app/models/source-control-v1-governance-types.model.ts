/** Wire types of the Source Control v1 governance API. */

import type {
  SourceControlJsonObject,
} from './source-control-v1-api.model';

export interface SourceControlContentAdmissionValidation {
  readonly valid: boolean;
  readonly preview: SourceControlJsonObject;
}

export interface SourceControlContentAdmissionCreation {
  readonly connection: SourceControlJsonObject;
  readonly revision: SourceControlJsonObject;
  readonly content: SourceControlJsonObject;
}

export interface SourceControlWorkspaceCatalogItem {
  readonly workspace_id: string;
  readonly enabled: boolean;
  readonly read_only: boolean;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlRegisteredRemoteCatalogItem {
  readonly remote_id: string;
  readonly kind: 'git' | 'github';
  readonly repository: string | null;
  readonly state: string;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlIndexProfileCatalogItem {
  readonly profile_id: string;
  readonly label: string;
  readonly description: string;
  readonly is_default: boolean;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlCatalogPage<T> {
  readonly items: readonly T[];
  readonly next_cursor: string | null;
  readonly capabilities: SourceControlJsonObject;
}

export type SourceControlWorkspaceCatalogPage =
  SourceControlCatalogPage<SourceControlWorkspaceCatalogItem>;
export type SourceControlRegisteredRemoteCatalogPage =
  SourceControlCatalogPage<SourceControlRegisteredRemoteCatalogItem>;
export type SourceControlIndexProfileCatalogPage =
  SourceControlCatalogPage<SourceControlIndexProfileCatalogItem>;

export interface SourceControlWorkspaceFolder {
  readonly folder_handle: string;
  readonly display_name: string;
  readonly capabilities: {
    readonly selection_only: true;
    readonly read_only: true;
    readonly path_exposed: false;
    readonly file_names_exposed: false;
    readonly folder_label_exposed: true;
  };
}

export interface SourceControlWorkspaceFolderPage {
  readonly items: readonly SourceControlWorkspaceFolder[];
  readonly capabilities: {
    readonly project_scoped: true;
    readonly raw_paths_exposed: false;
  };
}

export interface SourceControlWorkspaceFolderValidation {
  readonly validation_handle: string;
  readonly expires_at_epoch: number;
  readonly capabilities: {
    readonly read_only: true;
    readonly one_time: true;
    readonly path_exposed: false;
    readonly filename_exposed: false;
  };
}

export interface SourceControlWorkspaceRegistration {
  readonly workspace_id: string;
  readonly state: 'active';
  readonly read_only: true;
  readonly etag: string;
  readonly capabilities: {
    readonly selection_only: true;
    readonly path_exposed: false;
    readonly filename_exposed: false;
  };
}

export type SourceControlGitAuthorizationKind =
  | 'github_app'
  | 'github_oauth'
  | 'generic_git';

export type SourceControlGitAuthorizationState =
  | 'active'
  | 'revoked'
  | 'scope_loss';

export type SourceControlGitAuthorizationNextAction =
  | 'revoke'
  | 'record_scope_loss';

export interface SourceControlGitAuthorizationView {
  readonly authorization_ref: string;
  readonly authorization_kind: SourceControlGitAuthorizationKind;
  readonly repository: string | null;
  readonly authorization_state: SourceControlGitAuthorizationState;
  readonly granted_scopes: readonly string[];
  readonly credential_configured: boolean;
  readonly persisted: boolean;
  readonly current_revision: number;
  readonly etag: string | null;
  readonly next_actions: readonly SourceControlGitAuthorizationNextAction[];
}

export interface SourceControlGitAuthorizationPage {
  readonly items: readonly SourceControlGitAuthorizationView[];
  readonly next_cursor: string | null;
}

export type SourceControlGitAuthorizationHealthStatus =
  | 'healthy'
  | 'degraded'
  | 'unavailable';

export interface SourceControlGitAuthorizationHealth {
  readonly status: SourceControlGitAuthorizationHealthStatus;
  readonly reason_code: string | null;
  readonly provider_status: SourceControlGitAuthorizationHealthStatus;
  readonly connector_ready: {
    readonly github_repository: boolean;
    readonly generic_git: boolean;
  };
  readonly registration_count: number;
  readonly active_registration_count: number;
}

export type SourceControlPublicRemoteProvider =
  | 'github_public'
  | 'https_git';

export type SourceControlPublicRemoteIntent =
  | {
      readonly provider: 'github_public';
      readonly owner: string;
      readonly repository: string;
      readonly requested_ref: string;
    }
  | {
      readonly provider: 'https_git';
      readonly host: string;
      readonly repository: string;
      readonly requested_ref: string;
    };

export interface SourceControlPublicRemoteValidation {
  readonly validation_handle: string;
  readonly provider: SourceControlPublicRemoteProvider;
  readonly requested_ref: string;
  readonly commit_sha: string;
  readonly expires_at_epoch: number;
  readonly capabilities: SourceControlPublicRemoteCapabilities;
}

export interface SourceControlPublicRemoteCreation {
  readonly remote_id: string;
  readonly provider: SourceControlPublicRemoteProvider;
  readonly commit_sha: string;
  readonly state: string;
  readonly capabilities: SourceControlPublicRemoteCapabilities;
}

export interface SourceControlPublicRemoteCapabilities {
  readonly connector_type: 'github' | 'git';
  readonly credential_mode: 'none';
  readonly remote_url_exposed: false;
  readonly immutable_validation?: true;
}

export interface SourceControlGrantPreset {
  readonly schema: 'ananta.source-control.grant-preset.v1';
  readonly preset_id: string;
  readonly label: string;
  readonly description: string;
  readonly operation: string;
  readonly transformation: string;
  readonly purpose: string;
  readonly max_duration_seconds: number;
}

export interface SourceControlGrant {
  readonly schema: 'ananta.source-control.grant-admin-item.v1';
  readonly grant_id: string;
  readonly grant_family_id: string;
  readonly version: number;
  readonly source_revision_id: string;
  readonly destination_id: string;
  readonly preset_id: string | null;
  readonly operation: string;
  readonly transformation: string;
  readonly purpose: string;
  readonly policy_version: string;
  readonly state: string;
  readonly issued_at: string;
  readonly expires_at: string;
  readonly expired: boolean;
  readonly etag: string;
}

export interface SourceControlGrantPresetPage {
  readonly items: readonly SourceControlGrantPreset[];
  readonly next_cursor: string | null;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlGrantPage {
  readonly schema: 'ananta.source-control.grant-admin-list.v1';
  readonly items: readonly SourceControlGrant[];
  readonly next_cursor: string | null;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlGrantMutationResult {
  readonly grant: SourceControlGrant;
  readonly capabilities: SourceControlJsonObject;
}
