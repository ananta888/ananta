import asyncio

from src.models import ModelPool


def test_model_pool_status():
    pool = ModelPool()
    pool.register("prov", "mod", limit=2)
    asyncio.run(pool.acquire("prov", "mod"))
    status = pool.status()
    assert status == {"prov": {"mod": {"limit": 2, "in_use": 1, "waiters": 0}}}
    pool.release("prov", "mod")
