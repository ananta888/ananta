"""Opt-in bounded file-SQLite connection reuse, independent of domain policy."""

from sqlalchemy.pool import NullPool, QueuePool


def file_sqlite_pool_options(capacity: int) -> dict:
    if type(capacity) is not int or not 0 <= capacity <= 64:
        raise ValueError("sqlite_pool_capacity_invalid")
    if capacity == 0:
        return {"poolclass": NullPool}
    # Reuse DBAPI connections, not Sessions, transactions or authorization
    # results. SQLAlchemy's default rollback-on-return remains enabled. Never
    # allocate overflow connections or stall a control request for 30 seconds.
    return {
        "poolclass": QueuePool,
        "pool_size": capacity,
        "max_overflow": 0,
        "pool_timeout": 0.25,
    }
