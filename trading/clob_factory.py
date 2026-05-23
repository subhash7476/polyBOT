"""
clob_factory.py — Single source of truth for V2 SDK imports and contract addresses.

All other modules should import ClobClient, OrderArgs, etc. from here rather
than directly from py_clob_client_v2, so that the SDK version can be swapped
in one place and paper-mode tests remain fast (SDK is imported lazily).
"""

# ---------------------------------------------------------------------------
# V2 contract addresses
# ---------------------------------------------------------------------------

PMCT_TOKEN = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF_EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"  # verify before going live
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
COLLATERAL_OFFRAMP = "0x2957922Eb93258b93368531d39fAcCA3B4dC5854"

# ---------------------------------------------------------------------------
# SDK import guard
# ---------------------------------------------------------------------------


def _import_sdk():
    """Return the py_clob_client_v2 module, raising a clear error if absent."""
    try:
        import py_clob_client_v2
        return py_clob_client_v2
    except ImportError:
        raise ImportError(
            "py-clob-client-v2 is not installed. Run: pip install py-clob-client-v2\n"
            "Do NOT reinstall py_clob_client (V1) — it is incompatible with V2 contracts."
        )


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def build_clob_client(
    private_key: str,
    chain_id: int = 137,
    sig_type: int = 0,
    funder: str | None = None,
):
    """Construct and return a V2 ClobClient instance.

    V2 constructor signature (confirmed via help(ClobClient.__init__)):
        __init__(self, host, chain_id, key=None, creds=None,
                 signature_type=None, funder=None,
                 builder_config=None, use_server_time=False,
                 retry_on_error=False)

    Note: host and chain_id are positional; key is keyword-only.
    """
    import os
    from config import POLYMARKET_CLOB_URL
    from utils.logger import get_logger
    sdk = _import_sdk()
    log = get_logger(__name__)

    # Optional builder attribution. When BUILDER_ADDRESS + BUILDER_CODE are set,
    # every order placed by this client gets stamped with the builder_code so
    # Polymarket recognises the trade as coming from a registered builder.
    builder_address = os.getenv("BUILDER_ADDRESS", "").strip()
    builder_code = os.getenv("BUILDER_CODE", "").strip()
    builder_config = None
    if builder_address and builder_code:
        from py_clob_client_v2.clob_types import BuilderConfig
        builder_config = BuilderConfig(
            builder_address=builder_address,
            builder_code=builder_code,
        )
        log.info(f"builder_config set: addr={builder_address[:10]}... code={builder_code[:10]}...")

    client = sdk.ClobClient(
        POLYMARKET_CLOB_URL,
        chain_id,
        key=private_key,
        signature_type=sig_type,
        funder=funder or None,
        builder_config=builder_config,
    )

    # L2 API credentials. Prefer explicit env vars (builder-generated API key);
    # fall back to deriving from L1 signature for backward compatibility.
    api_key = os.getenv("POLY_API_KEY", "").strip()
    api_secret = os.getenv("POLY_API_SECRET", "").strip()
    api_passphrase = os.getenv("POLY_PASSPHRASE", "").strip()

    if api_key and api_secret and api_passphrase:
        from py_clob_client_v2.clob_types import ApiCreds
        client.set_api_creds(ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        ))
        log.info("CLOB L2 creds loaded from env (POLY_API_KEY/SECRET/PASSPHRASE)")
    else:
        try:
            creds = client.create_or_derive_api_key()
            client.set_api_creds(creds)
            log.info("CLOB L2 creds derived from L1 signature")
        except Exception as exc:
            log.error(
                f"CLOB L2 api-key derivation failed — heartbeat/cancel/order-query "
                f"will not work: {exc}"
            )
    return client


# ---------------------------------------------------------------------------
# Type re-exports — sentinel when SDK not installed (keeps paper-mode tests fast)
# ---------------------------------------------------------------------------


class _MissingSDK:
    def __init__(self, name):
        self._name = name

    def __call__(self, *a, **kw):
        raise ImportError(
            f"{self._name} unavailable — run: pip install py-clob-client-v2"
        )

    def __getattr__(self, item):
        raise ImportError(
            f"{self._name}.{item} unavailable — run: pip install py-clob-client-v2"
        )


try:
    from py_clob_client_v2 import OrderArgs, OrderType, OpenOrderParams
except ImportError:
    OrderArgs = _MissingSDK("OrderArgs")        # type: ignore[assignment]
    OrderType = _MissingSDK("OrderType")        # type: ignore[assignment]
    OpenOrderParams = _MissingSDK("OpenOrderParams")  # type: ignore[assignment]
