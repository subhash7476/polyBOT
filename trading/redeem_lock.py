"""
trading/redeem_lock.py — Singleton flag lock preventing concurrent redemptions.

Cross-platform (no fcntl). Since the bot runs in a single asyncio process,
a simple module-level flag suffices. Uses a threading.Lock for the flag
mutation to be safe if ever called from threads.
"""
import threading

_mutex = threading.Lock()
_held = False


class RedeemLock:
    """Non-reentrant flag lock. acquire() returns False immediately if already held."""

    def acquire(self) -> bool:
        global _held
        with _mutex:
            if _held:
                return False
            _held = True
            return True

    def release(self) -> None:
        global _held
        with _mutex:
            _held = False
