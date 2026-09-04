"""Shared constants and errors for development workflow keyrings."""

from scripts import dev_workflow_identity_documents as _identity_documents

_UPGRADABLE_WORKER_CAPABILITY_SETS = _identity_documents.UPGRADABLE_WORKER_CAPABILITY_SETS
WorkerRegistrationSpec = _identity_documents.WorkerRegistrationSpec
_registration_document = _identity_documents.registration_document

_AUTH_KEY_ID = "dev-workflow-auth-v1"
_DISPATCH_KEY_ID = "dev-workflow-dispatch-v1"
_SIGNING_FILENAME = "workflow-auth-signing-keyring.json"
_VERIFICATION_FILENAME = "workflow-auth-verification-keyring.json"
_DISPATCH_FILENAME = "workflow-dispatch-keyring.json"
_REGISTRATION_KEYRING_FILENAME = "worker-registration-keyring.json"
_HUB_SERVICE_TOKEN_FILENAME = "hub-service-token"
_HUB_SESSION_KEY_FILENAME = "hub-session-signing-key"
_WORKER_SERVICE_TOKEN_FILENAME = "worker-service-token"
_WORKER_REGISTRATION_TOKEN_FILENAME = "worker-registration-token"
_WORKER_SESSION_KEY_FILENAME = "worker-session-signing-key"
_SOURCE_ACCESS_KEYRING_FILENAME = "source-access-hmac-keyring.json"
_SOURCE_ACCESS_KEY_ID = "dev-source-access-v1"
_TRANSACTION_FILENAME = ".bootstrap-transaction.json"
_TRANSACTION_SCHEMA = "ananta.dev-workflow-bootstrap-transaction.v1"
_STAGING_PREFIX = ".bootstrap-staging-"
_MAX_KEYRING_BYTES = 65_536
_AUTHORIZATION_DOCUMENTS = frozenset({"signing", "verification", "dispatch"})
_IDENTITY_DOCUMENTS = frozenset(
    {
        "registration_keyring",
        "hub_service_token",
        "hub_session_key",
        "alpha_service_token",
        "alpha_registration_token",
        "alpha_session_key",
        "beta_service_token",
        "beta_registration_token",
        "beta_session_key",
    }
)
_SOURCE_ACCESS_DOCUMENTS = frozenset({"source_access_keyring"})
_LEGACY_ALL_DOCUMENTS = _AUTHORIZATION_DOCUMENTS | _IDENTITY_DOCUMENTS
_ALL_DOCUMENTS = _LEGACY_ALL_DOCUMENTS | _SOURCE_ACCESS_DOCUMENTS


class DevWorkflowKeyringBootstrapError(RuntimeError):
    """Raised when a local keyring set cannot be trusted or completed."""
