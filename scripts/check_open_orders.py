"""Diagnostic: confirm the bot's current resting orders on the CLOB by ID."""
import os
from config import POLY_PRIVATE_KEY, SIGNATURE_TYPE, FUNDER_ADDRESS, POLYMARKET_CLOB_URL
from trading.clob_factory import _import_sdk

sig_type = int(os.getenv("SIGNATURE_TYPE", str(SIGNATURE_TYPE)))
funder = os.getenv("FUNDER_ADDRESS", FUNDER_ADDRESS) or None
sdk = _import_sdk()
clob = sdk.ClobClient(
    POLYMARKET_CLOB_URL, 137, key=POLY_PRIVATE_KEY,
    signature_type=sig_type, funder=funder,
)
clob.set_api_creds(clob.derive_api_key())

# Current orders placed by the bot at 23:19:45-49 (order_id, label).
ORDERS = [
    ("0xb8222c4ebb27a2290013663b46139cc6a7ea0d4216c9f02bbcc4c9caf2afb8ef", "Oh Se-hoon BUY"),
    ("0x5a2d80a0eeda0d05c5a5fa26519af9ce0f349d360965233232ebc5e869dfa2b0", "Oh Se-hoon SELL"),
    ("0xf42f75dcd36914219d205f2bbda7763ce140e0930cdb34ca68ca9e36b5ec1645", "Kim Farington BUY"),
    ("0x9f8a30577c94032eedc919f7882f07bdbf258671c3e496c143ff3f57f66db2d2", "Kim Farington SELL"),
    ("0x8ed656bfc10f5f1c818c522db88c66ba4ded0a778e9acc48c6ec666fe659fc0f", "US GDP BUY"),
    ("0x867c81c4563db2a179bc7343f9451540080579704be0bbca37acba37baef8f73", "US GDP SELL"),
    ("0x1ef17acfd34e6c143f331b5cf96000418afdb263e55bd6f5527a392590685c05", "Spencer Pratt BUY"),
    ("0x7d2153c2e58188dfc38fbb4def40b501f42ebfc21cb1071d6e9c7929270f61d6", "Spencer Pratt SELL"),
]

print(f"wallet/funder: {funder}  sig_type: {sig_type}\n")
for oid, label in ORDERS:
    try:
        o = clob.get_order(oid)
        print(f"  {label:22s} status={o.get('status')} "
              f"size={o.get('original_size')} matched={o.get('size_matched')} "
              f"price={o.get('price')}")
    except Exception as exc:
        print(f"  {label:22s} lookup failed: {exc!r}")
