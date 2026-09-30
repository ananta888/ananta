/**
 * Public governance contract of the Source Control v1 API (catalogs,
 * workspaces, Git authorizations, public remotes and grants).
 *
 * The implementation is split by responsibility and re-exported here so
 * existing imports keep working:
 * - source-control-v1-governance-types.model: wire types
 * - source-control-v1-governance-validation.model: shared fail-closed
 *   primitive validators
 * - source-control-v1-public-remote.model: public remote intents/results
 * - source-control-v1-workspace.model: workspace folders/registrations
 * - source-control-v1-catalog.model: content admission and catalog pages
 * - source-control-v1-git-authorization.model: Git authorization views
 * - source-control-v1-grant.model: grant presets and grants
 */

export type {
  SourceControlCatalogPage,
  SourceControlContentAdmissionCreation,
  SourceControlContentAdmissionValidation,
  SourceControlGitAuthorizationHealth,
  SourceControlGitAuthorizationHealthStatus,
  SourceControlGitAuthorizationKind,
  SourceControlGitAuthorizationNextAction,
  SourceControlGitAuthorizationPage,
  SourceControlGitAuthorizationState,
  SourceControlGitAuthorizationView,
  SourceControlGrant,
  SourceControlGrantMutationResult,
  SourceControlGrantPage,
  SourceControlGrantPreset,
  SourceControlGrantPresetPage,
  SourceControlIndexProfileCatalogItem,
  SourceControlIndexProfileCatalogPage,
  SourceControlPublicRemoteCapabilities,
  SourceControlPublicRemoteCreation,
  SourceControlPublicRemoteIntent,
  SourceControlPublicRemoteProvider,
  SourceControlPublicRemoteValidation,
  SourceControlRegisteredRemoteCatalogItem,
  SourceControlRegisteredRemoteCatalogPage,
  SourceControlWorkspaceCatalogItem,
  SourceControlWorkspaceCatalogPage,
  SourceControlWorkspaceFolder,
  SourceControlWorkspaceFolderPage,
  SourceControlWorkspaceFolderValidation,
  SourceControlWorkspaceRegistration,
} from './source-control-v1-governance-types.model';
export {
  parsePublicRemoteCreation,
  parsePublicRemoteIntent,
  parsePublicRemoteValidation,
} from './source-control-v1-public-remote.model';
export {
  assertSourceControlWorkspaceEtag,
  parseWorkspaceFolderPage,
  parseWorkspaceFolderValidation,
  parseWorkspaceRegistration,
} from './source-control-v1-workspace.model';
export {
  parseContentAdmissionCreation,
  parseContentAdmissionValidation,
  parseIndexProfileCatalogPage,
  parseRegisteredRemoteCatalogPage,
  parseWorkspaceCatalogPage,
} from './source-control-v1-catalog.model';
export {
  assertSourceControlGitAuthorizationEtag,
  parseGitAuthorizationHealth,
  parseGitAuthorizationPage,
  parseGitAuthorizationView,
} from './source-control-v1-git-authorization.model';
export {
  parseGrantMutationResult,
  parseGrantPage,
  parseGrantPresetPage,
} from './source-control-v1-grant.model';
