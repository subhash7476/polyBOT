"""
setup/allowances.py — Set Polymarket contract USDC spending allowances.

Run once before live trading:
    python -m setup.allowances

Approves CTF Exchange V2, NegRisk CTF Exchange V2, NegRisk Adapter, and
Collateral Onramp to spend PMCT (Polymarket collateral token, max uint256)
from your wallet on Polygon.

Requirements: POLY_PRIVATE_KEY and RPC_URL in .env
"""
import sys
import time
from dotenv import load_dotenv
import os

load_dotenv()

PMCT_TOKEN = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"

SPENDERS = [
    ("CTF Exchange V2",         "0xE111180000d2663C0091e4f400237545B87B996B"),
    ("NegRisk CTF Exchange V2", "0xe2222d279d744050d28e00520010520000310F59"),
    ("NegRisk Adapter",         "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),  # verify V2 address
    ("Collateral Onramp",       "0x93070a847efEf7F70739046A929D47a521F5B8ee"),
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
        address=Web3.to_checksum_address(PMCT_TOKEN),
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
