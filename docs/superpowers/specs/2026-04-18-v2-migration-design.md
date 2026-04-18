---
name: Polymarket V2 Migration
description: Migrate bot from py-clob-client V1 + USDC.e to py-clob-client-v2 + PMCT collateral token, using a clob_factory adapter to isolate the SDK dependency
type: project
---

# Polymarket V2 Migration — Design Spec

**Date:** 2026-04-18  
**Deadline:** 2026-04-22 (V2 goes live; no V1 fallback after cutover)  
**Approach:** Option B — minimal SDK swap with adapter isolation

## Context

Polymarket launched CTF Exchange V2 on 2026-04-06, going live 2026-04-22 with ~1 hour downtime. Key changes:

- **New collateral token (PMCT):** ERC20 wrapper around USDC/USDC.e replacing `USDC_BRIDGED`
- **New exchange contracts:** CTF Exchange V2 + NegRisk CTF Exchange V2 at new addresses
- **New Python SDK:** `py-clob-client-v2` (replaces `py_clob_client==0.34.6`)
- **Order struct changes:** remove `feeRateBps`, `nonce`, `taker`; constructor switches to options-dict form
- **All open limit orders wiped** during cutover (positions and funds safe)

The bot is currently in paper mode with no live allowances set on-chain.

## Contract Addresses

| Name | Old | New |
|---|---|---|
| Collateral token | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` (USDC.e) | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` (PMCT) |
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` | `0xE111180000d2663C0091e4f400237545B87B996B` |
| NegRisk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80b` | `0xe2222d279d744050d28e00520010520000310F59` |
| NegRisk Adapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` | Verify unchanged before going live |
| Collateral Onramp | — | `0x93070a847efEf7F70739046A929D47a521F5B8ee` |
| Collateral Offramp | — | `0x2957922Eb93258b93368531d39fAcCA3B4dC5854` |

## Architecture

### New File: `trading/clob_factory.py`

Single file that owns:
- All V2 SDK imports (`py_clob_client_v2`)
- `ClobClient` construction (options-dict constructor)
- V2 contract address constants (`PMCT_TOKEN`, `CTF_EXCHANGE_V2`, `NEG_RISK_EXCHANGE_V2`)
- Re-exports of SDK types (`OrderArgs`, `OrderType`, `OpenOrderParams`) so call sites import from one place

```python
PMCT_TOKEN           = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF_EXCHANGE_V2      = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"

def build_clob_client(private_key, chain_id=137, sig_type=0, funder=None):
    from py_clob_client_v2.client import ClobClient
    return ClobClient(host=CLOB_URL, key=private_key, chain_id=chain_id, ...)
```

All three existing call sites import from `clob_factory` instead of from `py_clob_client` directly.

## File-by-File Changes

### `requirements.txt`
- Remove: `py_clob_client==0.34.6`
- Add: `py-clob-client-v2` (latest from PyPI)

### `trading/clob_factory.py` *(new)*
- Define `PMCT_TOKEN`, `CTF_EXCHANGE_V2`, `NEG_RISK_EXCHANGE_V2` constants
- `build_clob_client(private_key, chain_id, sig_type, funder) -> ClobClient`
- Re-export `OrderArgs`, `OrderType`, `OpenOrderParams` from V2 SDK

### `trading/executor.py`
- Remove: `from py_clob_client.client import ClobClient`
- Add: `from trading.clob_factory import build_clob_client`
- Replace `ClobClient(...)` constructor call with `build_clob_client(...)`
- No changes to `place_order` / `cancel_order` logic (method signatures unchanged in V2)

### `maker/order_manager.py`
- Remove: `from py_clob_client.clob_types import OrderArgs, OrderType`
- Add: `from trading.clob_factory import OrderArgs, OrderType`
- Verify `post_order(signed, orderType=OrderType.GTC, post_only=True)` still valid in V2

### `maker/fill_poller.py`
- Remove: `from py_clob_client.clob_types import OpenOrderParams`
- Add: `from trading.clob_factory import OpenOrderParams`

### `trading/redeem.py`
- Remove: `USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"`
- Add: `from trading.clob_factory import PMCT_TOKEN`
- Replace `USDC_BRIDGED` → `PMCT_TOKEN` in `redeemPositions()` call
- Update `CTF_EXCHANGE` constant to V2 address `0xE111180000d2663C0091e4f400237545B87B996B`

### `setup/allowances.py`
- Replace `USDC_BRIDGED` → `PMCT_TOKEN = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"`
- Update `SPENDERS` list to V2 exchange addresses:
  - CTF Exchange V2: `0xE111180000d2663C0091e4f400237545B87B996B`
  - NegRisk CTF Exchange V2: `0xe2222d279d744050d28e00520010520000310F59`
  - NegRisk Adapter: verify address before going live
  - Add CollateralOnramp: `0x93070a847efEf7F70739046A929D47a521F5B8ee`

## Error Handling

- `build_clob_client` raises `ImportError` with a clear message if `py-clob-client-v2` is not installed (guards against accidental V1 reinstall)
- No changes needed to retry logic in `executor.py` — it already catches all exceptions

## Testing

- Existing paper-mode tests require no changes (SDK not imported in paper mode)
- Add `tests/trading/test_clob_factory.py`:
  - Verify factory raises `ImportError` gracefully if SDK absent
  - Verify constants are non-empty strings with `0x` prefix
- `setup/allowances.py`: run manually against Polygon Amoy testnet before going live

## Open Items

- **NegRisk Adapter address**: Confirm unchanged (`0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`) by checking Polymarket docs once they publish final V2 contract list
- **V2 SDK package name on PyPI**: Confirm exact install name (`py-clob-client-v2` vs `py_clob_client_v2`) once available
- **`post_only` flag**: Verify `post_order(..., post_only=True)` is still supported in V2 — if removed, update `order_manager.py` accordingly
