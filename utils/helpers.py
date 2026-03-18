import asyncio
import functools
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable)


def async_retry(max_attempts: int = 5, base_delay: float = 1.0):
    """Exponential backoff retry for async functions."""
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60.0)
        return wrapper  # type: ignore
    return decorator
