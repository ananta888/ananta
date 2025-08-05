import asyncio
import pytest

from src.models import ModelPool


def test_model_pool_status():
    pool = ModelPool()
    pool.register("prov", "mod", limit=2)
    asyncio.run(pool.acquire("prov", "mod"))
    status = pool.status()
    assert status == {"prov": {"mod": {"limit": 2, "in_use": 1, "waiters": 0}}}
    pool.release("prov", "mod")


async def _use_slot(pool: ModelPool) -> None:
    async with pool.slot("prov", "mod"):
        assert pool.status()["prov"]["mod"]["in_use"] == 1
        raise RuntimeError("boom")


def test_context_manager_releases_on_error():
    pool = ModelPool()
    pool.register("prov", "mod", limit=1)
    with pytest.raises(RuntimeError):
        asyncio.run(_use_slot(pool))
    assert pool.status()["prov"]["mod"]["in_use"] == 0
