"""Decisive test: post heartbeats every 5s while polling the newest order batch.
If fresh orders survive well past ~25s during active heartbeating, the keep-alive
is the heartbeat. If they still die fast, it is not."""
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
OK_RE = re.compile(r"ORDER OK \| id=(0x[0-9a-f]+) ")

def newest_ids():
    hits = []
    with open(LOG, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            m = OK_RE.search(line)
            if m:
                hits.append(m.group(1))
    return tuple(hits[-2:])

start = time.time()
last_seen = None
first_seen_at = {}
while time.time() - start < 400:
    try:
        hb = c.post_heartbeat()
    except Exception as e:
        hb = f"ERR {e}"
    ids = newest_ids()
    statuses = []
    for oid in ids:
        try:
            o = c.get_order(oid)
            s = o.get("status")
        except Exception:
            s = "ERR"
        statuses.append(s)
        if oid not in first_seen_at and s == "LIVE":
            first_seen_at[oid] = time.time()
    age = ""
    if ids and ids[0] in first_seen_at:
        age = f" age~{time.time()-first_seen_at[ids[0]]:.0f}s"
    tag = "NEW" if ids != last_seen else ""
    print(f"  t+{time.time()-start:4.0f}s hb={'ok' if 'heartbeat_id' in str(hb) else hb} "
          f"orders={[i[:10] for i in ids]} {statuses}{age} {tag}")
    last_seen = ids
    time.sleep(5)
