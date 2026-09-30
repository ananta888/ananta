/**
 * Public contract of the canonical Source Control v1 API.
 *
 * The implementation is split by responsibility and re-exported here so
 * existing imports keep working:
 * - source-control-v1-api-types.model: wire types and the contract error
 * - source-control-v1-api-validation.model: identifier/cursor/ETag guards,
 *   envelopes and primitive JSON validators
 * - source-control-v1-api-parsers.model: projection, lifecycle, bulk,
 *   event, access and connection parsers
 * - source-control-v1-context-policy-parsers.model: Context Policy parsers
 */

export {
  SOURCE_CONTROL_V1_ERROR_SCHEMA,
  SOURCE_CONTROL_V1_RESPONSE_SCHEMA,
  SourceControlV1ContractError,
} from './source-control-v1-api-types.model';
export type {
  ContextPolicyDiagnostic,
  ContextPolicyDiagnosticSeverity,
  ContextPolicyLintResult,
  ContextPolicyPreview,
  ContextPolicyState,
  ContextPolicySummary,
  ContextPolicySummaryPage,
  ContextPolicyVersion,
  ContextPolicyVersionDetail,
  ContextPolicyVersionPage,
  SourceControlAccessDecision,
  SourceControlAccessDecisionKind,
  SourceControlAccessMatrix,
  SourceControlActiveIndex,
  SourceControlBulkPlan,
  SourceControlBulkPlanItem,
  SourceControlBulkResult,
  SourceControlBulkResultItem,
  SourceControlBulkTarget,
  SourceControlConnection,
  SourceControlConnectionCreation,
  SourceControlConnectionValidation,
  SourceControlErrorEnvelope,
  SourceControlExplorationResult,
  SourceControlIndexComparison,
  SourceControlIndexRecord,
  SourceControlJobEvent,
  SourceControlJobEventPage,
  SourceControlJobEventType,
  SourceControlJson,
  SourceControlJsonObject,
  SourceControlLifecycleAcknowledgement,
  SourceControlMutation,
  SourceControlNextAction,
  SourceControlOperationReceipt,
  SourceControlProjection,
  SourceControlProjectionConnection,
  SourceControlProjectionDetail,
  SourceControlProjectionPage,
  SourceControlRunPage,
} from './source-control-v1-api-types.model';
export {
  assertSourceControlActivePointerEtag,
  assertSourceControlCursor,
  assertSourceControlEtag,
  assertSourceControlIdempotencyKey,
  assertSourceControlOpaqueId,
  assertSourceControlSha256,
  parseSourceControlEnvelope,
  parseSourceControlErrorEnvelope,
} from './source-control-v1-api-validation.model';
export {
  parseSourceControlAccessDecision,
  parseSourceControlAccessMatrix,
  parseSourceControlBulkPlan,
  parseSourceControlBulkResult,
  parseSourceControlConnectionCreation,
  parseSourceControlConnectionValidation,
  parseSourceControlExplorationResult,
  parseSourceControlIndexComparison,
  parseSourceControlJobEventPage,
  parseSourceControlLifecycleAcknowledgement,
  parseSourceControlOperationReceipt,
  parseSourceControlProjection,
  parseSourceControlProjectionPage,
  parseSourceControlRunPage,
} from './source-control-v1-api-parsers.model';
export {
  parseContextPolicyDocument,
  parseContextPolicyLintResult,
  parseContextPolicyPreview,
  parseContextPolicySummaryPage,
  parseContextPolicyVersion,
  parseContextPolicyVersionPage,
} from './source-control-v1-context-policy-parsers.model';
