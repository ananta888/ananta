"""Pi capability policy over the shared transactional Native authority reader."""

from agent.services.native_result_authority import SQLAlchemyNativeResultAuthority


class SQLAlchemyPiResultAuthority:
    def __init__(self, *, key_ring):
        self._authority = SQLAlchemyNativeResultAuthority(
            key_ring=key_ring, required_capabilities=("coding.agent.pi",), reason_prefix="pi_native_result",
        )

    def require_current(self, *, session, command, task, now):
        try:
            return self._authority.require_current(session=session, command=command, task=task, now=now)
        except ValueError as exc:
            if str(exc) == "native_result_authority_missing":
                raise ValueError("pi_native_result_authority_missing") from exc
            raise
