# Operational Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all operational gaps vs. polyterminal: log rotation, feed staleness gate, multi-wallet support, order retry, USDC balance tracking, on-chain allowance setup, redemption system, and Telegram remote control.

**Architecture:** Eight independent but sequentially buildable components layered on top of the existing signal pipeline. No existing trading logic is touched — all changes are additive to infrastructure, config, and execution layers.

**Tech Stack:** Python 3.10+, `web3>=6.0.0`, `eth-account>=0.8.0`, `requests>=2.28.0` (already present), `python-telegram-bot>=20.0` (new), existing `asyncio`/`httpx`/`websockets` stack.

---

## File Map

### New files
| File | Responsibility |
|---|---|
| `trading/balance.py` | `BalanceState` dataclass + async polling loop for USDC wallet balance |
| `trading/redeem.py` | Single-market CTF redemption via web3 |
| `trading/redeemall.py` | Batch redemption: classify positions, redeem all eligible |
| `trading/redeem_lock.py` | `asyncio.Lock` singleton preventing concurrent redemption |
| `setup/allowances.py` | One-shot: approve CTF Exchange + NegRisk contracts for USDC spend |
| `telegram_bot.py` | Long-poll Telegram bot: /status /balance /stop /restart |
| `tests/trading/test_balance.py` | Tests for BalanceState polling |
| `tests/trading/test_redeem.py` | Tests for redeem logic (mocked web3) |
| `tests/trading/test_redeemall.py` | Tests for batch redemption classification |
| `tests/setup/test_allowances.py` | Tests for allowance approval logic (mocked web3) |

### Modified files
| File | Change |
|---|---|
| `utils/logger.py` | Add rotating file handler (3h rotation, 24 backups) alongside stdout |
| `config.py` | Add `SIGNATURE_TYPE`, `FUNDER_ADDRESS`, `RPC_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MAX_TRADE_SIZE_USDC` |
| `market/state.py` | Add `last_feed_update: dict` timestamps to `FeedState`; add `BalanceState` |
| `market/clob_monitor.py` | Set `feeds.last_feed_update["clob"]` on each price event |
| `feeds/microstructure.py` | Set `feeds.last_feed_update["microstructure"]` after each poll |
| `trading/executor.py` | Add retry logic; use `SIGNATURE_TYPE`/`FUNDER_ADDRESS` for wallet address |
| `main.py` | Wire `BalanceState` polling loop + auto-redeem check after fills |
| `CLAUDE.md` | Document all new modules, env vars, and operational procedures |
| `README.md` | Update setup steps, env var table, new scripts |

---

## Task 1: Log Rotation

**Files:**
- Modify: `utils/logger.py`
- Test: `tests/test_logger.py` (new)

- [ ] **Step 1: Write failing test**

```python
# tests/test_logger.py
import logging
from pathlib import Path
from utils.logger import get_logger

def test_logger_writes_to_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    log = get_logger("test_rot", log_dir=str(tmp_path))
    log.info("hello")
    files = list(tmp_path.glob("test_rot*.log"))
    assert len(files) == 1
    assert "hello" in files[0].read_text()

def test_logger_still_streams_to_stdout(tmp_path, capsys):
    log = get_logger("test_stdout", log_dir=str(tmp_path))
    log.info("stdout check")
    captured = capsys.readouterr()
    assert "stdout check" in captured.out
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/test_logger.py -v
```

- [ ] **Step 3: Implement rotating logger**

Replace `utils/logger.py` entirely:

```python
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_DIR = Path("logs")
_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str, log_dir: str | None = None) -> logging.Logger:
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    # stdout handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # rotating file handler — 3h rotation, 24 backups (3 days)
    log_dir_path = Path(log_dir) if log_dir else _LOG_DIR
    log_dir_path.mkdir(parents=True, exist_ok=True)
    fh = TimedRotatingFileHandler(
        log_dir_path / f"{name}.log",
        when="H",
        interval=3,
        backupCount=24,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.propagate = False
    _loggers[name] = logger
    return logger
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_logger.py -v
pytest -x -q  # confirm nothing else broke
```

- [ ] **Step 5: Commit**

```bash
git add utils/logger.py tests/test_logger.py
git commit -m "feat: add rotating file handler to logger (3h rotation, 24 backups)"
```

---

## Task 2: Feed Staleness Gate

**Files:**
- Modify: `market/state.py` — add `last_feed_update` dict to `FeedState`
- Modify: `market/clob_monitor.py` — stamp timestamp on price events
- Modify: `feeds/microstructure.py` — stamp timestamp after each poll
- Test: `tests/market/test_staleness.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/market/test_staleness.py
import time
from market.state import FeedState

def test_feed_fresh_when_recent():
    fs = FeedState()
    fs.last_feed_update["clob"] = time.time()
    fs.last_feed_update["microstructure"] = time.time()
    assert fs.is_fresh(max_age_seconds=5)

def test_feed_stale_when_old():
    fs = FeedState()
    fs.last_feed_update["clob"] = time.time() - 10
    fs.last_feed_update["microstructure"] = time.time()
    assert not fs.is_fresh(max_age_seconds=5)

def test_feed_stale_when_missing():
    fs = FeedState()
    # neither key set
    assert not fs.is_fresh(max_age_seconds=5)
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/market/test_staleness.py -v
```

- [ ] **Step 3: Add `last_feed_update` + `is_fresh()` to `FeedState`**

In `market/state.py`, add to the `FeedState` dataclass:

```python
import time
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class FeedState:
    # ... existing fields ...
    last_feed_update: dict = field(default_factory=dict)  # feed_name → unix timestamp

    def is_fresh(self, max_age_seconds: float = 5.0) -> bool:
        """True only if both clob and microstructure feeds updated recently."""
        required = ("clob", "microstructure")
        now = time.time()
        return all(
            now - self.last_feed_update.get(k, 0) <= max_age_seconds
            for k in required
        )
```

- [ ] **Step 4: Stamp timestamp in `CLOBMonitor._handle_price()`**

In `market/clob_monitor.py`, inside `_handle_price()` after updating `cs.best_bid`/`cs.best_ask`:

```python
import time
# at end of _handle_price, outside the lock (or inside — time.time() is safe):
self._state.feeds.last_feed_update["clob"] = time.time()
```

- [ ] **Step 5: Stamp timestamp in `MicrostructureFeed`**

In `feeds/microstructure.py`, after each successful poll cycle, add:

```python
import time
await state.update_feeds(last_feed_update={**state.feeds.last_feed_update, "microstructure": time.time()})
```

Or, since `last_feed_update` is a mutable dict on `FeedState`, you can write directly (no lock needed for dict key set in asyncio single-thread context):

```python
self._state.feeds.last_feed_update["microstructure"] = time.time()
```

- [ ] **Step 6: Gate trading loop in `main.py`**

In `trading_loop()`, right after acquiring `feeds = state.feeds`, add:

```python
if not feeds.is_fresh():
    log.debug("feeds stale — skipping scan")
    continue
```

- [ ] **Step 7: Run tests — expect PASS**

```bash
pytest tests/market/test_staleness.py -v
pytest -x -q
```

- [ ] **Step 8: Commit**

```bash
git add market/state.py market/clob_monitor.py feeds/microstructure.py main.py tests/market/test_staleness.py
git commit -m "feat: feed staleness gate — block trading when clob/microstructure feeds > 5s old"
```

---

## Task 3: Multi-Wallet Support (SIGNATURE_TYPE)

**Files:**
- Modify: `config.py` — add `SIGNATURE_TYPE`, `FUNDER_ADDRESS`, `RPC_URL`
- Modify: `trading/executor.py` — derive correct wallet address from signature type
- Test: `tests/trading/test_executor.py` (extend existing)

- [ ] **Step 1: Write failing test**

```python
# In tests/trading/test_executor.py, add:
from unittest.mock import patch
import os

def test_executor_uses_funder_address_for_type1(monkeypatch):
    monkeypatch.setenv("SIGNATURE_TYPE", "1")
    monkeypatch.setenv("FUNDER_ADDRESS", "0xABCDEF")
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0x" + "a" * 64)
    # reload config to pick up env changes
    import importlib, config
    importlib.reload(config)
    from trading.executor import CLOBExecutor
    ex = CLOBExecutor(paper=True)
    assert ex.wallet_address == "0xABCDEF"

def test_executor_uses_private_key_address_for_type0(monkeypatch):
    monkeypatch.setenv("SIGNATURE_TYPE", "0")
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0x" + "a" * 64)
    import importlib, config
    importlib.reload(config)
    from trading.executor import CLOBExecutor
    from eth_account import Account
    ex = CLOBExecutor(paper=True)
    expected = Account.from_key("0x" + "a" * 64).address
    assert ex.wallet_address == expected
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/trading/test_executor.py -v
```

- [ ] **Step 3: Add vars to `config.py`**

```python
SIGNATURE_TYPE   = int(os.getenv("SIGNATURE_TYPE", "0"))   # 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE
FUNDER_ADDRESS   = os.getenv("FUNDER_ADDRESS", "")
RPC_URL          = os.getenv("RPC_URL", "https://polygon-rpc.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
MAX_TRADE_SIZE_USDC = float(os.getenv("MAX_TRADE_SIZE_USDC", "50"))
```

- [ ] **Step 4: Update `CLOBExecutor.__init__()` to derive `wallet_address`**

```python
from eth_account import Account
from config import SIGNATURE_TYPE, FUNDER_ADDRESS, POLY_PRIVATE_KEY

class CLOBExecutor:
    def __init__(self, private_key: str = POLY_PRIVATE_KEY, paper: bool = True):
        self._paper = paper
        self._private_key = private_key
        sig_type = int(os.getenv("SIGNATURE_TYPE", "0"))
        if sig_type in (1, 2) and FUNDER_ADDRESS:
            self.wallet_address = FUNDER_ADDRESS
        else:
            self.wallet_address = Account.from_key(private_key).address if private_key else ""
        if not paper and private_key:
            from py_clob_client.client import ClobClient
            self._clob = ClobClient(
                host=POLYMARKET_CLOB_URL,
                key=private_key,
                chain_id=137,
                signature_type=sig_type,
                funder=FUNDER_ADDRESS or None,
            )
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/trading/test_executor.py -v
pytest -x -q
```

- [ ] **Step 6: Commit**

```bash
git add config.py trading/executor.py tests/trading/test_executor.py
git commit -m "feat: SIGNATURE_TYPE 0/1/2 wallet support + config vars for RPC, Telegram"
```

---

## Task 4: Order Retry Logic

**Files:**
- Modify: `trading/executor.py` — wrap `place_order` in retry loop

- [ ] **Step 1: Write failing test**

```python
# In tests/trading/test_executor.py, add:
import asyncio
from unittest.mock import AsyncMock, patch

def test_place_order_retries_on_failure(monkeypatch):
    from trading.executor import CLOBExecutor
    ex = CLOBExecutor(paper=False, private_key="0x" + "a" * 64)

    call_count = 0
    async def fake_post(*a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("network error")
        return {"orderID": "ok123", "status": "live", "price": "0.6", "size": "10"}

    ex._clob = type("C", (), {"create_and_post_order": fake_post, "create_order": lambda *a, **kw: None})()
    result = asyncio.get_event_loop().run_until_complete(
        ex.place_order("tok_yes", "tok_no", "BUY_YES", 10.0, 0.6)
    )
    assert result.success
    assert call_count == 3
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/trading/test_executor.py::test_place_order_retries_on_failure -v
```

- [ ] **Step 3: Add retry to `place_order` in `trading/executor.py`**

```python
_MAX_RETRIES = 3
_RETRY_DELAY = 1.0  # seconds

async def place_order(self, yes_token_id, no_token_id, side, size, price) -> OrderResult:
    # ... validation unchanged ...
    actual_token = yes_token_id if side == "BUY_YES" else no_token_id
    if self._paper:
        # ... paper path unchanged ...

    last_exc = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = self._clob.create_and_post_order(
                self._clob.create_order(token_id=actual_token, price=price, size=size, side="BUY")
            )
            order_id = resp.get("orderID")
            log.info(f"ORDER | id={order_id} {side} size=${size:.2f} price={price:.4f}")
            return OrderResult(
                order_id=order_id,
                status=resp.get("status", "UNKNOWN"),
                filled_price=float(resp.get("price", price)),
                filled_size=float(resp.get("size", size)),
                token_used=actual_token,
            )
        except Exception as exc:
            last_exc = exc
            log.warning(f"order attempt {attempt}/{_MAX_RETRIES} failed: {exc}")
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAY)

    log.error(f"order failed after {_MAX_RETRIES} attempts: {last_exc}")
    return OrderResult(order_id=None, status="ERROR", filled_price=0.0, filled_size=0.0, error=str(last_exc))
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/trading/test_executor.py -v
pytest -x -q
```

- [ ] **Step 5: Commit**

```bash
git add trading/executor.py tests/trading/test_executor.py
git commit -m "feat: order retry logic — 3 attempts with 1s delay before returning ERROR"
```

---

## Task 5: USDC Balance Tracking

**Files:**
- Create: `trading/balance.py`
- Modify: `market/state.py` — add `BalanceState`
- Modify: `main.py` — start balance polling loop
- Test: `tests/trading/test_balance.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/trading/test_balance.py
import pytest
from unittest.mock import patch, AsyncMock
from trading.balance import BalancePoller, BalanceState

def test_balance_state_session_pnl():
    bs = BalanceState(session_start=100.0, current=95.0)
    assert bs.session_pnl == pytest.approx(-5.0)

@pytest.mark.asyncio
async def test_balance_poller_updates_state(monkeypatch):
    from market.state import AppState
    state = AppState()
    state.balance.session_start = 100.0

    async def fake_fetch(wallet):
        return 110.0

    poller = BalancePoller(state, wallet_address="0xABC", poll_interval=0)
    with patch.object(poller, "_fetch_usdc_balance", fake_fetch):
        await poller._poll_once()

    assert state.balance.current == 110.0
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/trading/test_balance.py -v
```

- [ ] **Step 3: Add `BalanceState` to `market/state.py`**

```python
@dataclass
class BalanceState:
    session_start: float = 0.0   # USDC at bot startup
    current: float = 0.0         # latest fetched balance
    last_update: float = 0.0     # unix timestamp

    @property
    def session_pnl(self) -> float:
        return self.current - self.session_start
```

Add to `AppState.__init__()`:
```python
self.balance = BalanceState()
```

- [ ] **Step 4: Create `trading/balance.py`**

```python
"""
trading/balance.py — USDC wallet balance polling.

Polls data-api.polymarket.com every 60s to track current USDC balance.
Sets session_start on first successful fetch.
"""
import asyncio
import httpx
from dataclasses import dataclass
from market.state import AppState
from utils.logger import get_logger

log = get_logger("balance")

_DATA_API = "https://data-api.polymarket.com"
_USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


class BalancePoller:
    def __init__(self, state: AppState, wallet_address: str, poll_interval: float = 60.0):
        self._state = state
        self._wallet = wallet_address
        self._interval = poll_interval

    async def start(self):
        log.info(f"balance poller starting for {self._wallet[:10]}...")
        while True:
            await self._poll_once()
            await asyncio.sleep(self._interval)

    async def _poll_once(self):
        try:
            balance = await self._fetch_usdc_balance(self._wallet)
            async with self._state._lock:
                if self._state.balance.session_start == 0.0:
                    self._state.balance.session_start = balance
                self._state.balance.current = balance
                import time
                self._state.balance.last_update = time.time()
            log.info(f"USDC balance: ${balance:.2f} (session P/L: ${self._state.balance.session_pnl:+.2f})")
        except Exception as exc:
            log.warning(f"balance fetch failed: {exc}")

    async def _fetch_usdc_balance(self, wallet: str) -> float:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_DATA_API}/value", params={"user": wallet})
            resp.raise_for_status()
            data = resp.json()
            # data-api returns portfolio value in USDC
            return float(data.get("portfolioValue", 0))
```

- [ ] **Step 5: Wire into `main.py`**

In `main()`, after creating `executor`:
```python
from trading.balance import BalancePoller
balance_poller = BalancePoller(state, wallet_address=executor.wallet_address)
```

Add `balance_poller.start()` to the `asyncio.gather()` call.

- [ ] **Step 6: Run tests — expect PASS**

```bash
pytest tests/trading/test_balance.py -v
pytest -x -q
```

- [ ] **Step 7: Commit**

```bash
git add trading/balance.py market/state.py main.py tests/trading/test_balance.py
git commit -m "feat: USDC balance tracking — session P/L polling every 60s from data-api"
```

---

## Task 6: On-Chain Allowance Setup

**Files:**
- Create: `setup/allowances.py`
- Create: `setup/__init__.py`
- Test: `tests/setup/test_allowances.py`

Note: Run once before first live trade. Not part of the bot daemon.

- [ ] **Step 1: Write failing tests**

```python
# tests/setup/test_allowances.py
from unittest.mock import MagicMock, patch
from setup.allowances import build_approve_tx, SPENDERS

def test_build_approve_tx_returns_dict():
    mock_contract = MagicMock()
    mock_contract.functions.approve.return_value.build_transaction.return_value = {
        "to": "0xSPENDER", "data": "0x...", "gas": 50000
    }
    tx = build_approve_tx(mock_contract, spender="0xSPENDER", account="0xACCOUNT", chain_id=137)
    assert tx["to"] == "0xSPENDER"

def test_spenders_has_three_entries():
    assert len(SPENDERS) == 3
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/setup/test_allowances.py -v
```

- [ ] **Step 3: Create `setup/__init__.py`** (empty)

- [ ] **Step 4: Create `setup/allowances.py`**

```python
"""
setup/allowances.py — Set Polymarket contract spending allowances.

Run once before live trading:
    python -m setup.allowances

Approves CTF Exchange, NegRisk CTF Exchange, and NegRisk Adapter to spend
bridged USDC from your wallet. Requires POLY_PRIVATE_KEY and RPC_URL in .env.
"""
import time
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv
import os

load_dotenv()

USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
SPENDERS = [
    ("CTF Exchange",        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
    ("NegRisk CTF Exchange","0xC5d563A36AE78145C45a50134d48A1215220f80b"),
    ("NegRisk Adapter",     "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),
]

APPROVE_ABI = [{
    "name": "approve", "type": "function", "stateMutability": "nonpayable",
    "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
    "outputs": [{"name": "", "type": "bool"}],
}]

MAX_UINT256 = 2**256 - 1


def build_approve_tx(contract, spender: str, account: str, chain_id: int) -> dict:
    return contract.functions.approve(
        Web3.to_checksum_address(spender), MAX_UINT256
    ).build_transaction({
        "from": Web3.to_checksum_address(account),
        "chainId": chain_id,
        "gas": 100_000,
    })


def run(rpc_url: str, private_key: str):
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    account = Account.from_key(private_key)
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_BRIDGED), abi=APPROVE_ABI
    )

    print(f"Wallet: {account.address}")
    for name, spender in SPENDERS:
        nonce = w3.eth.get_transaction_count(account.address)
        tx = build_approve_tx(usdc, spender, account.address, 137)
        tx["nonce"] = nonce
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        status = "OK" if receipt.status == 1 else "FAIL"
        print(f"  [{status}] {name}: {tx_hash.hex()}")
        time.sleep(2)


if __name__ == "__main__":
    rpc = os.getenv("RPC_URL", "https://polygon-rpc.com")
    key = os.getenv("POLY_PRIVATE_KEY", "")
    if not key:
        raise SystemExit("POLY_PRIVATE_KEY not set in .env")
    run(rpc, key)
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/setup/test_allowances.py -v
pytest -x -q
```

- [ ] **Step 6: Commit**

```bash
git add setup/__init__.py setup/allowances.py tests/setup/__init__.py tests/setup/test_allowances.py
git commit -m "feat: allowance setup script — approve CTF Exchange + NegRisk contracts for USDC"
```

---

## Task 7: Redemption System

**Files:**
- Create: `trading/redeem_lock.py`
- Create: `trading/redeem.py`
- Create: `trading/redeemall.py`
- Modify: `main.py` — call `redeemall` periodically
- Test: `tests/trading/test_redeem.py`, `tests/trading/test_redeemall.py`

### 7a: Redeem Lock

- [ ] **Step 1: Write failing test**

```python
# tests/trading/test_redeem.py
import asyncio
from trading.redeem_lock import RedeemLock

def test_lock_acquire_and_release():
    lock = RedeemLock()
    assert lock.acquire()   # not yet held
    lock.release()
    assert lock.acquire()   # releasable and re-acquirable
    lock.release()

def test_lock_blocks_concurrent():
    lock = RedeemLock()
    lock.acquire()
    assert not lock.acquire()  # already held — second acquire fails immediately
    lock.release()
```

- [ ] **Step 2: Create `trading/redeem_lock.py`**

```python
"""
trading/redeem_lock.py — Singleton asyncio lock preventing concurrent redemptions.

Cross-platform (no fcntl). Since the bot is a single process, asyncio.Lock suffices.
"""
import asyncio

_lock: asyncio.Lock | None = None
_held = False


class RedeemLock:
    """Non-reentrant flag lock — acquire() returns False immediately if already held."""

    def acquire(self) -> bool:
        global _held
        if _held:
            return False
        _held = True
        return True

    def release(self):
        global _held
        _held = False
```

- [ ] **Step 3: Run lock tests — expect PASS**

```bash
pytest tests/trading/test_redeem.py -v
```

### 7b: Single-Market Redemption

- [ ] **Step 4: Write failing tests for `redeem.py`**

```python
# append to tests/trading/test_redeem.py
from unittest.mock import MagicMock, patch
from trading.redeem import check_oracle_resolved, REDEEM_ABI

def test_check_oracle_resolved_returns_true_when_resolved():
    mock_ctf = MagicMock()
    mock_ctf.functions.payoutNumerators.return_value.call.side_effect = lambda: [1, 0]
    assert check_oracle_resolved(mock_ctf, condition_id="0xCOND") is True

def test_check_oracle_resolved_returns_false_when_unresolved():
    mock_ctf = MagicMock()
    mock_ctf.functions.payoutNumerators.return_value.call.side_effect = lambda: [0, 0]
    assert check_oracle_resolved(mock_ctf, condition_id="0xCOND") is False
```

- [ ] **Step 5: Create `trading/redeem.py`**

```python
"""
trading/redeem.py — Redeem a single resolved Polymarket market position.

Called automatically by the bot after detecting a resolved market, or manually:
    python -m trading.redeem <condition_id>
"""
import os
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv
from utils.logger import get_logger

load_dotenv()
log = get_logger("redeem")

CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

REDEEM_ABI = [
    {
        "name": "redeemPositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId",       "type": "bytes32"},
            {"name": "indexSets",         "type": "uint256[]"},
        ],
        "outputs": [],
    },
    {
        "name": "payoutNumerators",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "conditionId", "type": "bytes32"}, {"name": "index", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

USDC_BRIDGED     = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ZERO_BYTES32     = b"\x00" * 32
INDEX_SETS_BOTH  = [1, 2]   # redeem both YES and NO positions


def check_oracle_resolved(ctf_contract, condition_id: str) -> bool:
    """Return True if oracle has set a non-zero payout for this condition."""
    try:
        # payoutNumerators is indexed per outcome
        for i in range(2):
            val = ctf_contract.functions.payoutNumerators(
                Web3.to_bytes(hexstr=condition_id), i
            ).call()
            if val > 0:
                return True
        return False
    except Exception as exc:
        log.warning(f"oracle check failed for {condition_id}: {exc}")
        return False


def redeem_position(condition_id: str, rpc_url: str, private_key: str, max_retries: int = 3) -> bool:
    """Redeem a single condition. Returns True on success."""
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    account = Account.from_key(private_key)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_EXCHANGE), abi=REDEEM_ABI)

    if not check_oracle_resolved(ctf, condition_id):
        log.info(f"condition {condition_id[:10]} not yet resolved — skipping")
        return False

    for attempt in range(1, max_retries + 1):
        try:
            nonce = w3.eth.get_transaction_count(account.address)
            tx = ctf.functions.redeemPositions(
                Web3.to_checksum_address(USDC_BRIDGED),
                ZERO_BYTES32,
                Web3.to_bytes(hexstr=condition_id),
                INDEX_SETS_BOTH,
            ).build_transaction({
                "from": account.address,
                "chainId": 137,
                "gas": 200_000,
                "nonce": nonce,
            })
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status == 1:
                log.info(f"redeemed {condition_id[:10]}: {tx_hash.hex()}")
                return True
            log.warning(f"redeem tx failed on-chain: {tx_hash.hex()}")
        except Exception as exc:
            log.warning(f"redeem attempt {attempt}/{max_retries} failed: {exc}")

    return False
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
pytest tests/trading/test_redeem.py -v
```

### 7c: Batch Redemption

- [ ] **Step 7: Write failing tests for `redeemall.py`**

```python
# tests/trading/test_redeemall.py
from trading.redeemall import classify_positions

def test_classify_positions_splits_correctly():
    positions = [
        {"market": {"closed": True,  "resolved": True},  "size": 10},
        {"market": {"closed": True,  "resolved": False}, "size": 5},
        {"market": {"closed": False, "resolved": False}, "size": 3},
    ]
    redeemable, pending, active = classify_positions(positions)
    assert len(redeemable) == 1
    assert len(pending) == 1
    assert len(active) == 1
```

- [ ] **Step 8: Create `trading/redeemall.py`**

```python
"""
trading/redeemall.py — Batch redeem all eligible Polymarket positions.

Run manually:    python -m trading.redeemall
Called by bot:   await run_redeemall(state, executor)
Called by Telegram: /redeemall command
"""
import asyncio
import httpx
import os
from trading.redeem import redeem_position
from trading.redeem_lock import RedeemLock
from utils.logger import get_logger

log = get_logger("redeemall")

_DATA_API = "https://data-api.polymarket.com"
_lock = RedeemLock()


def classify_positions(positions: list[dict]) -> tuple[list, list, list]:
    """Split positions into (redeemable, pending, active)."""
    redeemable, pending, active = [], [], []
    for p in positions:
        mkt = p.get("market", {})
        if mkt.get("closed") and mkt.get("resolved"):
            redeemable.append(p)
        elif mkt.get("closed"):
            pending.append(p)
        else:
            active.append(p)
    return redeemable, pending, active


async def fetch_positions(wallet: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{_DATA_API}/positions", params={"user": wallet})
        resp.raise_for_status()
        return resp.json()


async def run_redeemall(wallet: str, rpc_url: str, private_key: str) -> int:
    """Fetch and redeem all eligible positions. Returns count of successful redemptions."""
    if not _lock.acquire():
        log.warning("redeemall already running — skipping")
        return 0

    redeemed = 0
    try:
        positions = await fetch_positions(wallet)
        redeemable, pending, active = classify_positions(positions)
        log.info(f"positions: {len(active)} active, {len(pending)} pending, {len(redeemable)} redeemable")

        for pos in redeemable:
            condition_id = pos.get("conditionId", "")
            if not condition_id:
                continue
            success = redeem_position(condition_id, rpc_url, private_key)
            if success:
                redeemed += 1
            await asyncio.sleep(2)   # rate limit between redemptions

    finally:
        _lock.release()

    log.info(f"redeemall complete: {redeemed}/{len(redeemable)} redeemed")
    return redeemed


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    wallet  = os.getenv("FUNDER_ADDRESS") or ""  # set from executor.wallet_address at runtime
    rpc     = os.getenv("RPC_URL", "https://polygon-rpc.com")
    key     = os.getenv("POLY_PRIVATE_KEY", "")
    if not key:
        sys.exit("POLY_PRIVATE_KEY not set")
    asyncio.run(run_redeemall(wallet, rpc, key))
```

- [ ] **Step 9: Wire periodic redeemall into `main.py`**

Add to `main()`:
```python
from trading.redeemall import run_redeemall
from config import RPC_URL

async def redeemall_loop(executor, interval: int = 900):
    """Check for redeemable positions every 15 minutes."""
    while True:
        await asyncio.sleep(interval)
        await run_redeemall(executor.wallet_address, RPC_URL, POLY_PRIVATE_KEY)
```

Add `redeemall_loop(executor)` to the `asyncio.gather()` call.

- [ ] **Step 10: Run all redeem tests — expect PASS**

```bash
pytest tests/trading/test_redeem.py tests/trading/test_redeemall.py -v
pytest -x -q
```

- [ ] **Step 11: Commit**

```bash
git add trading/redeem_lock.py trading/redeem.py trading/redeemall.py main.py \
        tests/trading/test_redeem.py tests/trading/test_redeemall.py
git commit -m "feat: redemption system — auto-redeem every 15m, single-market + batch, web3 CTF calls"
```

---

## Task 8: Telegram Remote Control

**Files:**
- Create: `telegram_bot.py`
- Test: `tests/test_telegram_bot.py`

Note: `python-telegram-bot>=20.0` must be added to `requirements.txt`. The bot runs as a separate process (`python telegram_bot.py`) or as a daemon thread launched from `main.py`. Separate process is simpler and avoids asyncio conflicts.

- [ ] **Step 1: Add dependency**

```bash
pip install "python-telegram-bot>=20.0"
echo "python-telegram-bot>=20.0" >> requirements.txt
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_telegram_bot.py
from telegram_bot import is_authorized, sanitize_output

def test_authorized_chat_passes():
    assert is_authorized(chat_id=12345, allowed_id="12345")

def test_unauthorized_chat_blocked():
    assert not is_authorized(chat_id=99999, allowed_id="12345")

def test_sanitize_strips_html():
    raw = "<b>hello</b> & <i>world</i>"
    clean = sanitize_output(raw)
    assert "<b>" not in clean
    assert "&amp;" in clean or "hello" in clean
```

- [ ] **Step 3: Run test — expect FAIL**

```bash
pytest tests/test_telegram_bot.py -v
```

- [ ] **Step 4: Create `telegram_bot.py`**

```python
"""
telegram_bot.py — Remote control for the Polymarket bot via Telegram.

Run as a separate process:
    python telegram_bot.py

Commands:
    /status    — show bot process status + feed freshness
    /balance   — show current USDC balance from data-api
    /redeemall — trigger batch redemption
    /stop      — kill trade process
    /restart   — restart trade process
    /help      — list commands
"""
import asyncio
import html
import os
import signal
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_ID  = os.getenv("TELEGRAM_CHAT_ID", "")
PID_FILE    = Path("bot.pid")


def is_authorized(chat_id: int, allowed_id: str) -> bool:
    return str(chat_id) == allowed_id


def sanitize_output(text: str) -> str:
    return html.escape(text)


def _get_pid() -> int | None:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except ValueError:
            return None
    return None


def _is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def _auth_check(update: Update) -> bool:
    if not is_authorized(update.effective_chat.id, ALLOWED_ID):
        await update.message.reply_text("Unauthorized.")
        return False
    return True


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _auth_check(update):
        return
    pid = _get_pid()
    running = _is_running(pid)
    status = f"Bot {'RUNNING' if running else 'STOPPED'} (pid={pid})"
    await update.message.reply_text(sanitize_output(status), parse_mode="HTML")


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _auth_check(update):
        return
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "import asyncio, httpx; "
             "r = asyncio.run(httpx.AsyncClient().get('https://data-api.polymarket.com/value', "
             "params={'user': __import__(\"os\").getenv(\"FUNDER_ADDRESS\",\"\")})); "
             "print(r.json())"],
            capture_output=True, text=True, timeout=30
        )
        await update.message.reply_text(sanitize_output(result.stdout or result.stderr))
    except Exception as exc:
        await update.message.reply_text(f"Error: {sanitize_output(str(exc))}")


async def cmd_redeemall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _auth_check(update):
        return
    await update.message.reply_text("Starting redeemall...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "trading.redeemall"],
            capture_output=True, text=True, timeout=300
        )
        out = (result.stdout + result.stderr)[-1000:]  # last 1000 chars
        await update.message.reply_text(sanitize_output(out) or "Done.")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("redeemall timed out (>5 min).")
    except Exception as exc:
        await update.message.reply_text(f"Error: {sanitize_output(str(exc))}")


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _auth_check(update):
        return
    pid = _get_pid()
    if not _is_running(pid):
        await update.message.reply_text("Bot not running.")
        return
    os.kill(pid, signal.SIGTERM)
    await asyncio.sleep(3)
    if _is_running(pid):
        os.kill(pid, signal.SIGKILL)
    await update.message.reply_text(f"Stopped (pid={pid}).")


async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _auth_check(update):
        return
    await cmd_stop(update, ctx)
    proc = subprocess.Popen([sys.executable, "main.py"])
    PID_FILE.write_text(str(proc.pid))
    await update.message.reply_text(f"Restarted (pid={proc.pid}).")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _auth_check(update):
        return
    await update.message.reply_text(
        "/status — process status\n"
        "/balance — USDC balance\n"
        "/redeemall — redeem winnings\n"
        "/stop — stop bot\n"
        "/restart — restart bot\n"
        "/help — this message"
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set in .env")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("balance",   cmd_balance))
    app.add_handler(CommandHandler("redeemall", cmd_redeemall))
    app.add_handler(CommandHandler("stop",      cmd_stop))
    app.add_handler(CommandHandler("restart",   cmd_restart))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.run_polling(poll_interval=30)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Write PID file from `main.py`**

At the top of `main()` in `main.py`:
```python
import os
from pathlib import Path
Path("bot.pid").write_text(str(os.getpid()))
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
pytest tests/test_telegram_bot.py -v
pytest -x -q
```

- [ ] **Step 7: Commit**

```bash
git add telegram_bot.py tests/test_telegram_bot.py requirements.txt main.py
git commit -m "feat: Telegram remote control — /status /balance /redeemall /stop /restart"
```

---

## Task 9: Docs Update

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md` (or create if absent)
- Modify: `.env.template`

- [ ] **Step 1: Update `CLAUDE.md`**

Add a new section after the existing architecture section:

```markdown
## Operational Infrastructure

### Log Files
All loggers write to both stdout and `logs/<name>.log` (3-hour rotation, 24 backups = 3 days).
Log directory is created automatically.

### Feed Staleness Gate
`FeedState.is_fresh(max_age_seconds=5)` — trading loop skips a scan if either `clob` or
`microstructure` feed has not updated in the last 5 seconds. Both feeds stamp
`feeds.last_feed_update[key]` on each successful update.

### Wallet Types (`SIGNATURE_TYPE`)
- `0` (default): EOA wallet — address derived from `POLY_PRIVATE_KEY`
- `1`: POLY_PROXY — Magic Link / email registration; set `FUNDER_ADDRESS`
- `2`: POLY_GNOSIS_SAFE — MetaMask/Phantom browser wallet; set `FUNDER_ADDRESS`
`CLOBExecutor.wallet_address` is always the correct address for the configured type.

### Order Retry
`CLOBExecutor.place_order()` retries up to 3 times with 1s delay before returning `ERROR`.

### USDC Balance Tracking
`BalancePoller` polls `data-api.polymarket.com/value` every 60s.
- Initialises `BalanceState.session_start` on first fetch
- `session_pnl = current - session_start`
Access via `state.balance` in any async context.

### Allowance Setup (run once before live trading)
```bash
python -m setup.allowances
```
Approves CTF Exchange, NegRisk CTF Exchange, NegRisk Adapter for max USDC spend.
Requires `POLY_PRIVATE_KEY` and `RPC_URL`.

### Redemption
Auto-redeemall runs every 15 minutes via `redeemall_loop()`.
Manual run: `python -m trading.redeemall`
Single market: `python -m trading.redeem <condition_id>`

Flow: fetch positions from data-api → classify (active/pending/redeemable) →
check oracle on-chain (`payoutNumerators`) → call `redeemPositions()` on CTF Exchange.

`RedeemLock` prevents concurrent redemptions (e.g., auto-loop vs. Telegram `/redeemall`).

### Telegram Bot
Run as a separate process: `python telegram_bot.py`
Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
The bot writes `bot.pid` on startup; Telegram /stop and /restart use this PID.

## Environment Variables (complete)

| Variable | Required | Default | Description |
|---|---|---|---|
| `POLY_PRIVATE_KEY` | Yes (live) | — | EOA private key with `0x` prefix |
| `SIGNATURE_TYPE` | No | `0` | `0`=EOA, `1`=POLY_PROXY, `2`=GNOSIS_SAFE |
| `FUNDER_ADDRESS` | If type 1/2 | — | Proxy wallet address |
| `RPC_URL` | No | `https://polygon-rpc.com` | Polygon RPC (Alchemy/Ankr recommended) |
| `BANKROLL_USDC` | No | `500` | Total bankroll for Kelly sizing |
| `PAPER` | No | `true` | `false` to go live (checklist required) |
| `TELEGRAM_BOT_TOKEN` | No | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | No | — | Your Telegram user/chat ID |
| `FRED_API_KEY` | No | — | Adds consensus CPI/GDP detail |
| `GLASSNODE_API_KEY` | No | — | On-chain netflow data |
| `MAX_TRADE_SIZE_USDC` | No | `50` | Hard cap per trade |
```

- [ ] **Step 2: Update `.env.template`**

```bash
# Wallet
POLY_PRIVATE_KEY=0x...
SIGNATURE_TYPE=0          # 0=EOA, 1=POLY_PROXY (Magic Link), 2=GNOSIS_SAFE (MetaMask)
FUNDER_ADDRESS=           # Required if SIGNATURE_TYPE=1 or 2

# Network
RPC_URL=https://polygon-rpc.com

# Trading
BANKROLL_USDC=500
PAPER=true
MAX_TRADE_SIZE_USDC=50

# Telegram (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Data APIs (optional — adds detail)
FRED_API_KEY=
GLASSNODE_API_KEY=
```

- [ ] **Step 3: Run full test suite — all passing**

```bash
pytest -v
```

- [ ] **Step 4: Final commit**

```bash
git add CLAUDE.md README.md .env.template
git commit -m "docs: document all operational infrastructure — logging, redemption, Telegram, wallet types"
```

---

## Dependencies to add to `requirements.txt`

```
web3>=6.0.0
eth-account>=0.8.0
python-telegram-bot>=20.0
```

Install:
```bash
pip install "web3>=6.0.0" "eth-account>=0.8.0" "python-telegram-bot>=20.0"
```
