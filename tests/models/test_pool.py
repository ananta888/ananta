import asyncio

from src.models import ModelPool


def test_parallel_acquire_is_serialized():
    pool = ModelPool()
    pool.register("openai", "gpt")
    order: list[str] = []

    async def worker(name: str, delay: float):
        await pool.acquire("openai", "gpt")
        order.append(f"start{name}")
        await asyncio.sleep(delay)
        order.append(f"end{name}")
        pool.release("openai", "gpt")

    async def main() -> None:
        await asyncio.gather(worker("1", 0.05), worker("2", 0.05))

    asyncio.run(main())
    assert order == ["start1", "end1", "start2", "end2"]


def test_release_unblocks_next_waiter():
    pool = ModelPool()
    pool.register("openai", "gpt")
    started = []

    async def first():
        await pool.acquire("openai", "gpt")
        started.append("first")
        await asyncio.sleep(0.05)
        pool.release("openai", "gpt")

    async def second():
        await pool.acquire("openai", "gpt")
        started.append("second")
        pool.release("openai", "gpt")

    async def main() -> None:
        await asyncio.gather(first(), second())

    asyncio.run(main())
    assert started == ["first", "second"]
