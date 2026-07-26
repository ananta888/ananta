"""Domain errors for backup and restore operations."""


class BackupError(RuntimeError):
    """Raised when an operation cannot complete without weakening safety."""
