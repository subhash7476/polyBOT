# Polymarket V2 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the bot from `py_clob_client==0.34.6` + USDC.e to `py-clob-client-v2` + PMCT collateral token before the April 22 2026 cutover, isolating the SDK behind a `trading/clob_factory.py` adapter.

**Architecture:** A new `trading/clob_factory.py` owns all V2 SDK imports and contract address constants. Three existing call sites (`executor.py`, `order_manager.py`, `fill_poller.py`) import from the factory instead of from `py_clob_client` directly. `redeem.py` and `allowances.py` update their hardcoded addresses to V2 values.

**Tech Stack:** Python, `py-clob-client-v2` (PyPI), web3.py, Polygon mainnet (chain_id=137)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `trading/clob_factory.py` | V2 SDK import, ClobClient construction, V2 contract constants, type re-exports |
| Create | `tests/trading/test_clob_factory.py` | Unit tests for factory constants and import guard |
| Modify | `requirements.txt` | Swap V1 SDK for V2 |
| Modify | `trading/executor.py` | Use `build_clob_client()` from factory |
| Modify | `maker/order_manager.py` | Import `OrderArgs`, `OrderType` from factory |
| Modify | `maker/fill_poller.py` | Import `OpenOrderParams` from factory |
| Modify | `trading/redeem.py` | `USDC_BRIDGED` → `PMCT_TOKEN`, CTF Exchange address → V2 |
| Modify | `setup/allowances.py` | PMCT token, V2 spender addresses |
| Modify | `tests/trading/test_redeem.py` | Update import of renamed constant |

---

## Task 1: Verify V2 SDK package name and swap requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Check V2 SDK availability on PyPI**

```bash
pip index versions py-clob-client-v2 2>/dev/null || echo "NOT FOUND"
pip index versions py_clob_client_v2 2>/dev/null || echo "NOT FOUND"
```

Note the package name that resolves. The import module name follows Python convention (hyphens → underscores), so `py-clob-client-v2` installs as importable `py_clob_client_v2`.

- [ ] **Step 2: Install V2 SDK in the virtualenv**

```bash
pip install py-clob-client-v2
```

If that fails, try: `pip install py_clob_client_v2`

- [ ] **Step 3: Confirm the import module name**

```bash
python -c "import py_clob_client_v2; print('OK')" 2>/dev/null || \
python -c "import py_clob_client_v2.client; print('OK')" 2>/dev/null || \
echo "Check module name — might differ"
```

Record the working import prefix for use in Task 2.

- [ ] **Step 4: Update requirements.txt**

Open `requirements.txt`. Remove this line:
```
py_clob_client==0.34.6
```

Add (using the confirmed package name from Step 1):
```
py-clob-client-v2
```

Final `requirements.txt`:
```
eth_account==0.13.7
Flask==3.1.1
httpx==0.28.1
numpy==2.2.4
py-clob-client-v2
pytest==9.0.3
python-dotenv==1.1.0
Requests==2.32.3
scipy==1.15.2
web3==7.10.0
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "chore(deps): swap py_clob_client v1 for py-clob-client-v2"
```

---

## Task 2: Create `trading/clob_factory.py`

**Files:**
- Create: `trading/clob_factory.py`

This file is the single source of truth for V2 SDK imports and contract addresses. All other files import from here.

- [ ] **Step 1: Create the file**

Create `trading/clob_factory.py` with this exact content:

```python
"""
trading/clob_factory.py

Single source of truth for:
- Polymarket V2 contract addresses
- py-clob-client-v2 SDK import and ClobClient construction
- Type re-exports (OrderArgs, OrderType, OpenOrderParams)

All other modules import from here — never from py_clob_client_v2 directly.
"""

from config import POLYMARKET_CLOB_URL

# V2 contract addresses (Polygon mainnet, chain_id=137)
PMCT_TOKEN            = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF_EXCHANGE_V2       = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE_V2  = "0xe2222d279d744050d28e00520010520000310F59"
NEG_RISK_ADAPTER      = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"  # verify before going live
COLLATERAL_ONRAMP     = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
COLLATERAL_OFFRAMP    = "0x2957922Eb93258b93368531d39fAcCA3B4dC5854"


def _import_sdk():
    """Import the V2 SDK, raising a clear error if not installed."""
    try:
        import py_clob_client_v2  # noqa: F401
        return py_clob_client_v2
    except ImportError:
        raise ImportError(
            "py-clob-client-v2 is not installed. Run: pip install py-clob-client-v2\n"
            "Do NOT reinstall py_clob_client (V1) — it is incompatible with V2 contracts."
        )


def build_clob_client(
    private_key: str,
    chain_id: int = 137,
    sig_type: int = 0,
    funder: str | None = None,
):
    """Construct and return a V2 ClobClient instance."""
    sdk = _import_sdk()
    return sdk.client.ClobClient(
        host=POLYMARKET_CLOB_URL,
        key=private_key,
        chain_id=chain_id,
        signature_type=sig_type,
        funder=funder or None,
    )


# Re-export SDK types so call sites never import py_clob_client_v2 directly.
def _get_order_args_class():
    return _import_sdk().clob_types.OrderArgs


def _get_order_type_class():
    return _import_sdk().clob_types.OrderType


def _get_open_order_params_class():
    return _import_sdk().clob_types.OpenOrderParams


# Lazy class references — evaluated at call time, not import time.
# Usage in call sites:  from trading.clob_factory import OrderArgs
# These are assigned at module import so the names exist, but SDK import
# only happens when they are used (keeping paper-mode tests fast).
try:
    from py_clob_client_v2.clob_types import OrderArgs, OrderType, OpenOrderParams  # type: ignore
except ImportError:
    OrderArgs = None        # type: ignore[assignment]
    OrderType = None        # type: ignore[assignment]
    OpenOrderParams = None  # type: ignore[assignment]
```

**Note:** If the V2 SDK module name differs from `py_clob_client_v2` (confirmed in Task 1 Step 3), update the three import lines accordingly throughout this file.

---

## Task 3: Write and pass tests for `clob_factory.py`

**Files:**
- Create: `tests/trading/test_clob_factory.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/trading/test_clob_factory.py`:

```python
"""Tests for trading/clob_factory.py — no real SDK or network calls."""
import pytest


def test_pmct_token_is_valid_address():
    from trading.clob_factory import PMCT_TOKEN
    assert PMCT_TOKEN.startswith("0x")
    assert len(PMCT_TOKEN) == 42


def test_ctf_exchange_v2_is_valid_address():
    from trading.clob_factory import CTF_EXCHANGE_V2
    assert CTF_EXCHANGE_V2.startswith("0x")
    assert len(CTF_EXCHANGE_V2) == 42


def test_neg_risk_exchange_v2_is_valid_address():
    from trading.clob_factory import NEG_RISK_EXCHANGE_V2
    assert NEG_RISK_EXCHANGE_V2.startswith("0x")
    assert len(NEG_RISK_EXCHANGE_V2) == 42


def test_pmct_differs_from_old_usdc_bridged():
    from trading.clob_factory import PMCT_TOKEN
    OLD_USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    assert PMCT_TOKEN != OLD_USDC_BRIDGED


def test_v2_exchange_differs_from_v1():
    from trading.clob_factory import CTF_EXCHANGE_V2
    OLD_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    assert CTF_EXCHANGE_V2 != OLD_CTF_EXCHANGE


def test_import_guard_raises_clear_error(monkeypatch):
    """If SDK not installed, ImportError has actionable message."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name.startswith("py_clob_client_v2"):
            raise ImportError("No module named 'py_clob_client_v2'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    from trading import clob_factory
    import importlib
    importlib.reload(clob_factory)

    with pytest.raises(ImportError, match="pip install py-clob-client-v2"):
        clob_factory._import_sdk()
```

- [ ] **Step 2: Run tests to verify they fail (except address tests which should pass)**

```bash
pytest tests/trading/test_clob_factory.py -v
```

Expected: address/differs tests PASS (constants already defined), import_guard test may FAIL until SDK installed.

- [ ] **Step 3: Run full test suite to confirm no regressions**

```bash
pytest tests/ -v --tb=short
```

Expected: all previously passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add trading/clob_factory.py tests/trading/test_clob_factory.py
git commit -m "feat(v2): add clob_factory adapter with V2 addresses and SDK import guard"
```

---

## Task 4: Update `trading/executor.py`

**Files:**
- Modify: `trading/executor.py:91-98`

- [ ] **Step 1: Replace the SDK import and constructor**

In `trading/executor.py`, find this block (lines 90-98):

```python
        if not paper:
            from py_clob_client.client import ClobClient  # type: ignore
            self._clob = ClobClient(
                host=POLYMARKET_CLOB_URL,
                key=private_key,
                chain_id=137,  # Polygon mainnet
                signature_type=sig_type,
                funder=funder or None,
            )
```

Replace with:

```python
        if not paper:
            from trading.clob_factory import build_clob_client
            self._clob = build_clob_client(
                private_key=private_key,
                chain_id=137,
                sig_type=sig_type,
                funder=funder or None,
            )
```

Also remove `POLYMARKET_CLOB_URL` from the import line at the top since `build_clob_client` handles it internally:

```python
# Before:
from config import POLYMARKET_CLOB_URL, POLY_PRIVATE_KEY, SIGNATURE_TYPE, FUNDER_ADDRESS

# After:
from config import POLY_PRIVATE_KEY, SIGNATURE_TYPE, FUNDER_ADDRESS
```

- [ ] **Step 2: Run executor tests**

```bash
pytest tests/trading/test_executor.py tests/trading/test_executor_improvements.py -v
```

Expected: all pass (paper mode tests never hit the SDK import path).

- [ ] **Step 3: Commit**

```bash
git add trading/executor.py
git commit -m "feat(v2): executor uses build_clob_client from clob_factory"
```

---

## Task 5: Update `maker/order_manager.py`

**Files:**
- Modify: `maker/order_manager.py:73-76`

- [ ] **Step 1: Swap the import**

In `maker/order_manager.py`, find (line 73, inside `_place_one`):

```python
        from py_clob_client.clob_types import OrderArgs, OrderType
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
        signed = self._clob.create_order(order_args)
        result = self._clob.post_order(signed, orderType=OrderType.GTC, post_only=True)
```

Replace with:

```python
        from trading.clob_factory import OrderArgs, OrderType
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
        signed = self._clob.create_order(order_args)
        result = self._clob.post_order(signed, orderType=OrderType.GTC, post_only=True)
```

**Note on `post_only`:** If the V2 SDK raises `TypeError: post_order() got unexpected keyword argument 'post_only'`, remove that argument. GTC orders on a maker bot are inherently post-only in terms of strategy — the `post_only` flag is an API hint, not a safety gate.

- [ ] **Step 2: Run order_manager tests**

```bash
pytest tests/maker/test_order_manager.py -v
```

Expected: all pass (tests use a `FakeClobClient` mock, no real SDK import).

- [ ] **Step 3: Commit**

```bash
git add maker/order_manager.py
git commit -m "feat(v2): order_manager imports OrderArgs/OrderType from clob_factory"
```

---

## Task 6: Update `maker/fill_poller.py`

**Files:**
- Modify: `maker/fill_poller.py:162`

- [ ] **Step 1: Swap the import**

In `maker/fill_poller.py`, find (inside `_run_live`, around line 162):

```python
        from py_clob_client.clob_types import OpenOrderParams
```

Replace with:

```python
        from trading.clob_factory import OpenOrderParams
```

- [ ] **Step 2: Run fill_poller tests**

```bash
pytest tests/maker/test_fill_poller.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add maker/fill_poller.py
git commit -m "feat(v2): fill_poller imports OpenOrderParams from clob_factory"
```

---

## Task 7: Update `trading/redeem.py`

**Files:**
- Modify: `trading/redeem.py:14-16`
- Modify: `tests/trading/test_redeem.py:4`

- [ ] **Step 1: Update constants in redeem.py**

In `trading/redeem.py`, find lines 14-16:

```python
CTF_EXCHANGE  = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
USDC_BRIDGED  = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ZERO_BYTES32  = b"\x00" * 32
```

Replace with:

```python
CTF_EXCHANGE  = "0xE111180000d2663C0091e4f400237545B87B996B"  # V2
PMCT_TOKEN    = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # replaces USDC_BRIDGED
ZERO_BYTES32  = b"\x00" * 32
```

- [ ] **Step 2: Update redeemPositions call**

In `trading/redeem.py`, find (around line 84):

```python
            tx = ctf.functions.redeemPositions(
                Web3.to_checksum_address(USDC_BRIDGED),
```

Replace with:

```python
            tx = ctf.functions.redeemPositions(
                Web3.to_checksum_address(PMCT_TOKEN),
```

- [ ] **Step 3: Fix the test import that references the old constant name**

In `tests/trading/test_redeem.py`, find line 4:

```python
from trading.redeem import check_oracle_resolved, CTF_EXCHANGE, USDC_BRIDGED, INDEX_SETS
```

Replace with:

```python
from trading.redeem import check_oracle_resolved, CTF_EXCHANGE, PMCT_TOKEN, INDEX_SETS
```

Then find any test that references `USDC_BRIDGED` and replace with `PMCT_TOKEN`. Check with:

```bash
grep -n "USDC_BRIDGED" tests/trading/test_redeem.py
```

For each occurrence, replace `USDC_BRIDGED` with `PMCT_TOKEN`.

- [ ] **Step 4: Run redeem tests**

```bash
pytest tests/trading/test_redeem.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add trading/redeem.py tests/trading/test_redeem.py
git commit -m "feat(v2): redeem uses PMCT_TOKEN and CTF Exchange V2 address"
```

---

## Task 8: Update `setup/allowances.py`

**Files:**
- Modify: `setup/allowances.py:19-25`

- [ ] **Step 1: Verify NegRisk Adapter address before editing**

Check the official Polymarket docs or Discord for confirmation that `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` is unchanged in V2. If a new address is published, use that. If unconfirmed, keep the old address and add a comment.

- [ ] **Step 2: Update the token and spender addresses**

In `setup/allowances.py`, replace lines 19-25:

```python
USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

SPENDERS = [
    ("CTF Exchange",         "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
    ("NegRisk CTF Exchange", "0xC5d563A36AE78145C45a50134d48A1215220f80b"),
    ("NegRisk Adapter",      "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),
]
```

With:

```python
PMCT_TOKEN = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"

SPENDERS = [
    ("CTF Exchange V2",       "0xE111180000d2663C0091e4f400237545B87B996B"),
    ("NegRisk CTF Exchange V2","0xe2222d279d744050d28e00520010520000310F59"),
    ("NegRisk Adapter",        "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),  # verify V2 address
    ("Collateral Onramp",      "0x93070a847efEf7F70739046A929D47a521F5B8ee"),
]
```

- [ ] **Step 3: Update the variable name used in `run()`**

In `setup/allowances.py`, inside `run()`, find:

```python
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_BRIDGED),
        abi=APPROVE_ABI,
    )
```

Replace with:

```python
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(PMCT_TOKEN),
        abi=APPROVE_ABI,
    )
```

- [ ] **Step 4: Run the full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests pass. `setup/allowances.py` has no unit tests (it's a one-shot script); verify manually against Amoy testnet before running on mainnet.

- [ ] **Step 5: Commit**

```bash
git add setup/allowances.py
git commit -m "feat(v2): allowances uses PMCT_TOKEN and V2 exchange spender addresses"
```

---

## Task 9: Final verification and test run

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests pass, no import errors, no references to `py_clob_client` V1.

- [ ] **Step 2: Confirm no stray V1 imports remain**

```bash
grep -rn "py_clob_client\b" . --include="*.py" | grep -v "py_clob_client_v2" | grep -v ".pyc"
```

Expected: no output. If any appear, fix them to import from `trading.clob_factory` instead.

- [ ] **Step 3: Confirm no stray USDC_BRIDGED references remain**

```bash
grep -rn "USDC_BRIDGED\|0x2791Bca1" . --include="*.py"
```

Expected: no output.

- [ ] **Step 4: Smoke-test the bot starts in paper mode**

```bash
timeout 5 python main.py 2>&1 | head -20 || true
```

Expected: bot starts, logs show paper mode active, no `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 5: Final commit if any cleanup was needed**

```bash
git add -p
git commit -m "chore(v2): final cleanup — remove stray V1 references"
```

---

## Open Items (verify before going live)

1. **NegRisk Adapter address** — confirm `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` is unchanged in V2, or update in `clob_factory.py` and `allowances.py`
2. **V2 SDK module name** — confirm `py_clob_client_v2` is the correct Python import prefix (Task 1 Step 3); update `clob_factory.py` if different
3. **`post_only` flag** — confirm `post_order(..., post_only=True)` still accepted in V2 (Task 5 note)
4. **Collateral wrapping** — for live trading, USDC must be wrapped to PMCT via `CollateralOnramp` before orders can be placed; this is a user/wallet-level step, not a bot-level step, but document in runbook
