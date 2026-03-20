"""
setup/allowances.py — Set Polymarket contract USDC spending allowances.

Run once before live trading:
    python -m setup.allowances

Approves CTF Exchange, NegRisk CTF Exchange, and NegRisk Adapter to spend
bridged USDC (max uint256) from your wallet on Polygon.

Requirements: POLY_PRIVATE_KEY and RPC_URL in .env
"""
import sys
import time
from dotenv import load_dotenv
import os

load_dotenv()

USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

SPENDERS = [
    ("CTF Exchange",         "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
    ("NegRisk CTF Exchange", "0xC5d563A36AE78145C45a50134d48A1215220f80b"),
    ("NegRisk Adapter",      "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),
]

APPROVE_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    }
]

MAX_UINT256 = 2**256 - 1


def build_approve_tx(contract, spender: str, account_address: str, chain_id: int) -> dict:
    """Build an approve() transaction dict (not yet signed)."""
    from web3 import Web3
    return contract.functions.approve(
        Web3.to_checksum_address(spender),
        MAX_UINT256,
    ).build_transaction({
        "from":    Web3.to_checksum_address(account_address),
        "chainId": chain_id,
        "gas":     100_000,
    })


def run(rpc_url: str, private_key: str) -> None:
    from web3 import Web3
    from eth_account import Account

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to RPC: {rpc_url}")

    account = Account.from_key(private_key)
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_BRIDGED),
        abi=APPROVE_ABI,
    )

    print(f"Wallet: {account.address}")
    print(f"Chain:  {w3.eth.chain_id}")

    for name, spender in SPENDERS:
        nonce = w3.eth.get_transaction_count(account.address)
        tx = build_approve_tx(usdc, spender, account.address, 137)
        tx["nonce"] = nonce
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        status = "OK" if receipt.status == 1 else "FAIL"
        print(f"  [{status}] {name}: {tx_hash.hex()}")
        time.sleep(2)


if __name__ == "__main__":
    rpc = os.getenv("RPC_URL", "https://polygon-rpc.com")
    key = os.getenv("POLY_PRIVATE_KEY", "")
    if not key:
        sys.exit("POLY_PRIVATE_KEY not set in .env")
    run(rpc, key)
