import threading
import time

from src.models.pool import ModelPool


def test_model_pool_limits_concurrency():
    pool = ModelPool()
    pool.register("prov", "model", 1)

    order = []

    def worker(idx):
        with pool.acquire("prov", "model"):
            order.append(idx)
            time.sleep(0.1)

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))

    t1.start()
    time.sleep(0.05)  # ensure t1 acquires before starting t2
    t2.start()

    t1.join()
    t2.join()

    assert order == [1, 2]
