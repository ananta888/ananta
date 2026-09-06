"""Domain-level retry classification without leaking filesystem details."""


class PersonaStorageRetryableError(ValueError):
    """Transient private-store failure; the immutable erasure receipt is resumable."""
