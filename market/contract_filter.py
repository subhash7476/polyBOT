from market.state import ContractState

_ALLOWED_CATEGORIES = {"crypto", "finance"}


def is_crypto_finance_market(market: dict) -> bool:
    return market.get("category", "").lower() in _ALLOWED_CATEGORIES


def meets_liquidity_threshold(contract: ContractState, min_usd: float = 10_000) -> bool:
    return contract.volume_usd >= min_usd
