import asyncio
from abc import ABC, abstractmethod
from utils.logger import get_logger


class BaseFeed(ABC):
    def __init__(self, name: str):
        self.name = name
        self.log = get_logger(name)
        self._running = False

    @abstractmethod
    async def _run(self):
        """Override: main feed coroutine (one connection lifetime)."""

    async def start(self):
        self._running = True
        self.log.info("starting")
        while self._running:
            try:
                await self._run()
            except Exception as exc:
                self.log.warning(f"feed error: {exc} — reconnecting in 5s")
                await asyncio.sleep(5)

    def stop(self):
        self._running = False
