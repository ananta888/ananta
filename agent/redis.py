import logging
import redis
from agent.config import settings

_redis_client = None


def get_redis_client():
    """
    Returns a global Redis client instance.
    Initializes it on the first call if REDIS_URL is configured.
    """
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    if not settings.redis_url:
        logging.info("Redis is not configured (REDIS_URL missing).")
        return None

    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        _redis_client = client
        logging.info("Redis connected successfully.")
        return _redis_client
    except Exception as e:
        logging.error(f"Failed to connect to Redis at {settings.redis_url}: {e}")
        return None
