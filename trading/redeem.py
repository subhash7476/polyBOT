"""
trading/redeem.py — Redeem a single resolved Polymarket market position.

Checks oracle resolution on-chain before calling redeemPositions().
Called automatically by redeemall.py, or manually:
    python -m trading.redeem <condition_id>
"""
import sys
import os
from utils.logger import get_logger

log = get_logger("redeem")

CTF_EXCHANGE  = "0xE111180000d2663C0091e4f400237545B87B996B"  # V2
PMCT_TOKEN    = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # replaces USDC_BRIDGED
ZERO_BYTES32  = b"\x00" * 32
INDEX_SETS    = [1, 2]   # redeem both YES and NO positions

CTF_ABI = [
    {
        "name": "redeemPositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken",    "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId",        "type": "bytes32"},
            {"name": "indexSets",          "type": "uint256[]"},
        ],
        "outputs": [],
    },
    {
        "name": "payoutNumerators",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "index",       "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


def check_oracle_resolved(ctf_contract, condition_id: str) -> bool:
    """Return True if at least one outcome payout numerator is non-zero."""
    try:
        condition_bytes = bytes.fromhex(condition_id.removeprefix("0x"))
        for i in range(2):
            val = ctf_contract.functions.payoutNumerators(condition_bytes, i).call()
            if val > 0:
                return True
        return False
    except Exception as exc:
        log.warning(f"oracle check failed for {condition_id[:12]}: {exc}")
        return False


def redeem_position(
    condition_id: str,
    rpc_url: str,
    private_key: str,
    max_retries: int = 3,
) -> bool:
    """Redeem a single condition. Returns True on success, False otherwise."""
    from web3 import Web3
    from eth_account import Account

    w3  = Web3(Web3.HTTPProvider(rpc_url))
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_EXCHANGE), abi=CTF_ABI
    )

    if not check_oracle_resolved(ctf, condition_id):
        log.info(f"condition {condition_id[:12]} not yet resolved — skipping")
        return False

    account = Account.from_key(private_key)
    condition_bytes = bytes.fromhex(condition_id.removeprefix("0x"))

    for attempt in range(1, max_retries + 1):
        try:
            nonce = w3.eth.get_transaction_count(account.address)
            tx = ctf.functions.redeemPositions(
                Web3.to_checksum_address(PMCT_TOKEN),
                ZERO_BYTES32,
                condition_bytes,
                INDEX_SETS,
            ).build_transaction({
                "from":    account.address,
                "chainId": 137,
                "gas":     200_000,
                "nonce":   nonce,
            })
            signed  = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status == 1:
                log.info(f"redeemed {condition_id[:12]}: {tx_hash.hex()}")
                return True
            log.warning(f"redeem tx failed on-chain: {tx_hash.hex()}")
        except Exception as exc:
            log.warning(f"redeem attempt {attempt}/{max_retries} failed: {exc}")

    return False


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    if len(sys.argv) < 2:
        sys.exit("Usage: python -m trading.redeem <condition_id>")
    rpc = os.getenv("RPC_URL", "https://polygon-rpc.com")
    key = os.getenv("POLY_PRIVATE_KEY", "")
    if not key:
        sys.exit("POLY_PRIVATE_KEY not set")
    success = redeem_position(sys.argv[1], rpc, key)
    sys.exit(0 if success else 1)
