"""Current Context Policy lookup using the caller's existing locked transaction."""

from sqlalchemy import select

from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.repositories.context_policy_lifecycle_repository import context_policy_version_from_row


class TransactionActiveContextPolicies:
    def __init__(self, session):
        if not session.in_transaction():
            raise ValueError('native_context_caller_transaction_required')
        self._session = session

    def active(self, *, tenant_id, project_id, policy_id):
        statement = select(ContextPolicyVersionDB).where(ContextPolicyVersionDB.tenant_id == tenant_id,
            ContextPolicyVersionDB.project_id == project_id, ContextPolicyVersionDB.policy_id == policy_id,
            ContextPolicyVersionDB.state == 'active').limit(2)
        if self._session.get_bind().dialect.name == 'postgresql':
            statement = statement.with_for_update()
        rows = self._session.execute(statement.execution_options(populate_existing=True)).scalars().all()
        if len(rows) > 1:
            raise ValueError('multiple_active_policy_snapshots')
        return context_policy_version_from_row(rows[0]) if rows else None
