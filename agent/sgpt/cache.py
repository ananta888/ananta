import json
from hashlib import md5
from pathlib import Path
from typing import Any, Callable, Generator


class Cache:
    """
    Decorator class that adds caching functionality to a function.
    """

    def __init__(self, length: int, cache_path: Path) -> None:
        """
        Initialize the Cache decorator.

        :param length: Integer, maximum number of cache files to keep.
        """
        self.length = length
        self.cache_path = cache_path
        from agent.adapters.namespace_memory_cache import NamespaceMemoryCache
        from agent.services.memory_storage_boundary import MemoryStorageBoundary

        self._storage = NamespaceMemoryCache(cache_path, boundary=MemoryStorageBoundary("hub-context-cache"),
                                              max_entries=max(1, min(length, 4096)))

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """
        The Cache decorator.

        :param func: The function to cache.
        :return: Wrapped function with caching.
        """

        def wrapper(*args: Any, **kwargs: Any) -> Generator[str, None, None]:
            caching = bool(kwargs.pop("caching", False))
            key = md5(json.dumps((func.__module__, func.__qualname__, args[1:], kwargs)).encode("utf-8"),
                      usedforsecurity=False).hexdigest()
            cached = self._storage.get(key) if caching else None
            if isinstance(cached, str):
                yield cached
                return
            result = ""
            for item in func(*args, **kwargs):
                result += item
                yield item
            if caching and "@FunctionCall" not in result:
                self._storage.put(key, result)

        return wrapper

    def _delete_oldest_files(self, max_files: int) -> None:
        """Compatibility hook. Eviction now occurs only inside the authorized namespace."""
        return None
