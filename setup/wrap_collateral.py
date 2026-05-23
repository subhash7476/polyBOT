"""
setup/wrap_collateral.py — Wrap USDC.e into pUSD for Polymarket V2 collateral.

Run once before live trading (or any time you bridge fresh USDC.e):
    python -m setup.wrap_collateral               # wrap entire USDC.e balance
    python -m setup.wrap_collateral --amount 500  # wrap exactly 500 USDC.e

Flow:
  1. Read USDC.e balance from wallet
  2. Approve CollateralOnramp to spend USDC.e
  3. Call onramp.wrap(USDC.e, wallet, amount)  -> mints pUSD 1:1
  4. Print before/after balances

Requirements: POLY_PRIVATE_KEY and RPC_URL in .env
"""

import sys
import time
import argparse
from dotenv import load_dotenv
import os

load_dotenv()

# Polygon mainnet addresses — source: docs.polymarket.com/resources/contracts
USDCE_ADDRESS  = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e (bridged)
PUSD_ADDRESS   = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # pUSD proxy
ONRAMP_ADDRESS = "0x93070a847efEf7F70739046A929D47a521F5B8ee"  # CollateralOnramp

DECIMALS = 6

ERC20_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs":  [{"name": "account", "type": "address"}],
        "outputs": [{"name": "",        "type": "uint256"}],
    },
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]

ONRAMP_ABI = [
    {
        # wrap(address _asset, address _to, uint256 _amount)
        # _asset  : must be USDC.e address
        # _to     : recipient of minted pUSD
        # _amount : in USDC.e base units (6 decimals)
        "name": "wrap",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_asset",  "type": "address"},
            {"name": "_to",     "type": "address"},
            {"name": "_amount", "type": "uint256"},
        ],
        "outputs": [],
    },
]


def _balance(contract, address: str) -> int:
    from web3 import Web3
    return contract.functions.balanceOf(Web3.to_checksum_address(address)).call()


def run(rpc_url: str, private_key: str, amount_usdc: float | None) -> None:
    from web3 import Web3
    from eth_account import Account

    from web3.middleware import ExtraDataToPOAMiddleware

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to RPC: {rpc_url}")

    account = Account.from_key(private_key)
    addr = account.address

    usdce  = w3.eth.contract(address=Web3.to_checksum_address(USDCE_ADDRESS),  abi=ERC20_ABI)
    pusd   = w3.eth.contract(address=Web3.to_checksum_address(PUSD_ADDRESS),   abi=ERC20_ABI)
    onramp = w3.eth.contract(address=Web3.to_checksum_address(ONRAMP_ADDRESS), abi=ONRAMP_ABI)

    usdce_bal = _balance(usdce, addr)
    pusd_bal  = _balance(pusd,  addr)

    print(f"Wallet:     {addr}")
    print(f"Chain:      {w3.eth.chain_id}")
    print(f"USDC.e:     {usdce_bal / 10**DECIMALS:.6f}")
    print(f"pUSD:       {pusd_bal  / 10**DECIMALS:.6f}")

    if usdce_bal == 0:
        print("\nNo USDC.e to wrap. Bridge funds first.")
        return

    if amount_usdc is not None:
        wrap_amount = int(amount_usdc * 10**DECIMALS)
        if wrap_amount > usdce_bal:
            raise ValueError(
                f"Requested {amount_usdc} USDC.e but wallet only has "
                f"{usdce_bal / 10**DECIMALS:.6f}"
            )
    else:
        wrap_amount = usdce_bal

    print(f"\nWrapping:   {wrap_amount / 10**DECIMALS:.6f} USDC.e -> pUSD")

    # Step 1: approve CollateralOnramp to spend USDC.e
    nonce = w3.eth.get_transaction_count(addr)
    approve_tx = usdce.functions.approve(
        Web3.to_checksum_address(ONRAMP_ADDRESS),
        wrap_amount,
    ).build_transaction({
        "from": addr, "chainId": 137, "gas": 100_000, "nonce": nonce,
    })
    signed = account.sign_transaction(approve_tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    status = "OK" if receipt.status == 1 else "FAIL"
    print(f"  [{status}] approve USDC.e -> Onramp: {tx_hash.hex()}")
    if receipt.status != 1:
        raise RuntimeError("approve() failed — aborting wrap")
    time.sleep(2)

    # Step 2: wrap(_asset=USDC.e, _to=wallet, _amount)
    nonce = w3.eth.get_transaction_count(addr)
    wrap_tx = onramp.functions.wrap(
        Web3.to_checksum_address(USDCE_ADDRESS),
        Web3.to_checksum_address(addr),
        wrap_amount,
    ).build_transaction({
        "from": addr, "chainId": 137, "gas": 150_000, "nonce": nonce,
    })
    signed = account.sign_transaction(wrap_tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    status = "OK" if receipt.status == 1 else "FAIL"
    print(f"  [{status}] wrap():                   {tx_hash.hex()}")
    if receipt.status != 1:
        raise RuntimeError("wrap() failed")

    pusd_after = _balance(pusd, addr)
    print(f"\npUSD after: {pusd_after / 10**DECIMALS:.6f}")
    print("Done. Run setup/allowances.py next if you haven't already.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wrap USDC.e into pUSD for Polymarket V2")
    parser.add_argument(
        "--amount", type=float, default=None,
        help="USDC.e amount to wrap (default: entire balance)",
    )
    args = parser.parse_args()

    rpc = os.getenv("RPC_URL", "https://polygon-rpc.com")
    key = os.getenv("POLY_PRIVATE_KEY", "")
    if not key:
        sys.exit("POLY_PRIVATE_KEY not set in .env")

    run(rpc, key, args.amount)
