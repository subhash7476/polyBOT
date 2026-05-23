"""Diagnostic: catch the next freshly-placed order batch and poll until cancelled,
pinning the exact LIVE -> CANCELED transition time."""
import os, re, time
from config import POLY_PRIVATE_KEY, SIGNATURE_TYPE, FUNDER_ADDRESS, POLYMARKET_CLOB_URL
from trading.clob_factory import _import_sdk

sdk = _import_sdk()
st = int(os.getenv("SIGNATURE_TYPE", str(SIGNATURE_TYPE)))
f = os.getenv("FUNDER_ADDRESS", FUNDER_ADDRESS) or None
c = sdk.ClobClient(POLYMARKET_CLOB_URL, 137, key=POLY_PRIVATE_KEY,
                   signature_type=st, funder=f)
c.set_api_creds(c.derive_api_key())

LOG = "logs/maker.order_manager.log"
OK_RE = re.compile(r"^(\S+ \S+) .*ORDER OK \| id=(0x[0-9a-f]+) ")
start = time.time()

def newest_ids():
    hits = []
    with open(LOG, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            m = OK_RE.match(line)
            if m:
                hits.append((m.group(1), m.group(2)))
    return hits[-2:]

print("waiting for next order batch...")
baseline = newest_ids()
ids = baseline
while ids == baseline and time.time() - start < 360:
    time.sleep(5)
    ids = newest_ids()

if ids == baseline:
    print("no new batch within 6 min - aborting")
    raise SystemExit

placed = time.time()
print(f"new batch detected: {[i[1][:14] for i in ids]}")
for _ in range(60):
    line = []
    for ts, oid in ids:
        o = c.get_order(oid)
        line.append(f"{oid[:12]}={o.get('status')}")
    elapsed = time.time() - placed
    print(f"  +{elapsed:5.0f}s  " + "  ".join(line))
    if all("CANCEL" in x or "MATCH" in x for x in line):
        print(f"=== both terminal after ~{elapsed:.0f}s since detection ===")
        break
    time.sleep(6)
