import asyncio
import time
import pytest
from app.core.lock_manager import KeyedLockManager


@pytest.mark.asyncio
async def test_keyed_lock_serializes_same_resource():
    lock_mgr = KeyedLockManager()
    execution_order = []

    async def worker(worker_id: int, delay: float):
        async with lock_mgr.lock("octocat/hello-world#42"):
            execution_order.append(f"start_{worker_id}")
            await asyncio.sleep(delay)
            execution_order.append(f"end_{worker_id}")

    task1 = asyncio.create_task(worker(1, 0.05))
    task2 = asyncio.create_task(worker(2, 0.01))

    await asyncio.gather(task1, task2)

    assert execution_order == ["start_1", "end_1", "start_2", "end_2"]


@pytest.mark.asyncio
async def test_keyed_lock_allows_distinct_resources_in_parallel():
    lock_mgr = KeyedLockManager()
    active_concurrent = 0
    max_concurrent = 0

    async def worker(resource_key: str):
        nonlocal active_concurrent, max_concurrent
        async with lock_mgr.lock(resource_key):
            active_concurrent += 1
            max_concurrent = max(max_concurrent, active_concurrent)
            await asyncio.sleep(0.04)
            active_concurrent -= 1

    task1 = asyncio.create_task(worker("octocat/hello-world#101"))
    task2 = asyncio.create_task(worker("octocat/hello-world#102"))

    await asyncio.gather(task1, task2)

    assert max_concurrent == 2
