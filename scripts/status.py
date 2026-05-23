"""One-shot bot health snapshot.

Usage:
    python -m scripts.status

Designed for "I shouldn't have to dig through logs": prints process state,
heartbeat freshness, order liveness, recent alerts, today's fills, and P&L
in a single screen. Read-only — does not touch the bot's running state.
"""

import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


# --- ANSI color helpers ------------------------------------------------------


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


_USE_COLOR = _supports_color()


def _c(code: str, s: str) -> str:
    return f"\x1b[{code}m{s}\x1b[0m" if _USE_COLOR else s


def red(s):    return _c("31", s)
def green(s):  return _c("32", s)
def yellow(s): return _c("33", s)
def dim(s):    return _c("2", s)
def bold(s):   return _c("1", s)


# --- Data collectors ---------------------------------------------------------


def process_status() -> dict:
    pid_file = ROOT / "bot.pid"
    if not pid_file.exists():
        return {"running": False, "pid": None, "reason": "bot.pid missing"}
    try:
        pid = int(pid_file.read_text().strip())
    except Exception as exc:
        return {"running": False, "pid": None, "reason": f"bad pid file: {exc}"}
    try:
        if os.name == "nt":
            import subprocess
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5,
            )
            alive = str(pid) in r.stdout
        else:
            os.kill(pid, 0)
            alive = True
    except Exception:
        alive = False
    return {"running": alive, "pid": pid, "reason": "alive" if alive else "no such process"}


_HEARTBEAT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3})\s+\|\s+INFO\s+\|\s+maker\.runner\s+\|\s+heartbeat OK"
)


def last_heartbeat() -> dict:
    path = ROOT / "logs" / "maker.runner.log"
    if not path.exists():
        return {"ok": False, "age_s": None, "reason": "log missing"}
    last_ts = None
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(8192, size)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(tail):
            m = _HEARTBEAT_RE.match(line)
            if m:
                last_ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                break
    except Exception as exc:
        return {"ok": False, "age_s": None, "reason": f"parse err: {exc}"}
    if last_ts is None:
        return {"ok": False, "age_s": None, "reason": "no heartbeat lines in recent tail"}
    age = time.time() - last_ts
    return {"ok": age <= 10.0, "age_s": age, "reason": ""}


def todays_fills() -> dict:
    today = date.today().isoformat()
    path = ROOT / "maker_data" / "maker_fills" / f"maker_fills_{today}.jsonl"
    if not path.exists():
        return {"count": 0, "cash_flow": 0.0, "realized_pnl": 0.0,
                "last_fill_age_s": None, "last_fill_side": None}
    count = 0
    cash_flow = 0.0
    realized = 0.0
    last_ts = 0.0
    last_side = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            count += 1
            cash_flow += float(r.get("cash_flow", 0.0))
            realized += float(r.get("realized_pnl", 0.0))
            ts = float(r.get("timestamp", r.get("ts", 0.0)))
            if ts > last_ts:
                last_ts = ts
                last_side = r.get("side")
    return {
        "count": count,
        "cash_flow": round(cash_flow, 4),
        "realized_pnl": round(realized, 4),
        "last_fill_age_s": (time.time() - last_ts) if last_ts else None,
        "last_fill_side": last_side,
    }


def lifetime_pnl() -> dict:
    path = ROOT / "maker_data" / "maker_lifetime.json"
    if not path.exists():
        return {"total_fills": 0, "total_cash_pnl": 0.0, "total_realized_pnl": 0.0}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"total_fills": 0, "total_cash_pnl": 0.0, "total_realized_pnl": 0.0}
    return {
        "total_fills": d.get("total_fills", 0),
        "total_cash_pnl": d.get("total_cash_pnl", 0.0),
        "total_realized_pnl": d.get("total_realized_pnl", 0.0),
    }


def recent_alerts(hours: float = 1.0) -> list[dict]:
    path = ROOT / "logs" / "alerts.log"
    if not path.exists():
        return []
    cutoff = time.time() - hours * 3600
    out: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            parts = [p.strip() for p in line.split("|", 4)]
            if len(parts) < 4:
                continue
            try:
                ts = time.mktime(time.strptime(parts[0], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                continue
            if ts < cutoff:
                continue
            out.append({
                "ts": parts[0], "severity": parts[1], "key": parts[2],
                "message": parts[3] if len(parts) > 3 else "",
            })
    return out[-20:]


# --- Renderer ----------------------------------------------------------------


def _fmt_age(secs: float | None) -> str:
    if secs is None:
        return dim("never")
    if secs < 60:
        return f"{secs:.0f}s ago"
    if secs < 3600:
        return f"{secs/60:.1f}m ago"
    return f"{secs/3600:.1f}h ago"


def render() -> int:
    proc = process_status()
    hb = last_heartbeat()
    fills = todays_fills()
    life = lifetime_pnl()
    alerts = recent_alerts(1.0)
    exit_code = 0

    print()
    print(bold("=" * 70))
    print(bold(f"  POLYMARKET MAKER BOT - STATUS @ {time.strftime('%Y-%m-%d %H:%M:%S')}"))
    print(bold("=" * 70))

    if proc["running"]:
        print(f"  Process       {green('UP')}   pid={proc['pid']}")
    else:
        print(f"  Process       {red('DOWN')} {dim(proc['reason'])}")
        exit_code = 2

    if hb["age_s"] is None:
        print(f"  Heartbeat     {red('UNKNOWN')} {dim(hb['reason'])}")
        exit_code = max(exit_code, 1)
    elif hb["age_s"] <= 10.0:
        print(f"  Heartbeat     {green('OK')}   last {_fmt_age(hb['age_s'])}")
    elif hb["age_s"] <= 30.0:
        print(f"  Heartbeat     {yellow('STALE')} last {_fmt_age(hb['age_s'])} (Polymarket TTL ~10s)")
        exit_code = max(exit_code, 1)
    else:
        print(f"  Heartbeat     {red('DEAD')} last {_fmt_age(hb['age_s'])} - orders auto-cancelled")
        exit_code = max(exit_code, 2)

    print()
    print(bold("  TODAY"))
    print(f"    Fills            {fills['count']}")
    print(f"    Last fill        {_fmt_age(fills['last_fill_age_s'])}  {dim('side=' + str(fills['last_fill_side']))}")
    cf = fills["cash_flow"]
    rp = fills["realized_pnl"]
    print(f"    Cash flow        {green(f'${cf:+.2f}') if cf >= 0 else red(f'${cf:+.2f}')}")
    print(f"    Realized P&L     {green(f'${rp:+.2f}') if rp >= 0 else red(f'${rp:+.2f}')}")

    print()
    print(bold("  LIFETIME"))
    print(f"    Total fills      {life['total_fills']}")
    tcf = life["total_cash_pnl"]
    trp = life["total_realized_pnl"]
    print(f"    Total cash P&L   {green(f'${tcf:+.2f}') if tcf >= 0 else red(f'${tcf:+.2f}')}")
    print(f"    Total realized   {green(f'${trp:+.2f}') if trp >= 0 else red(f'${trp:+.2f}')}")

    print()
    print(bold("  ALERTS (last 1h, max 20)"))
    if not alerts:
        print(f"    {green('none')}")
    else:
        exit_code = max(exit_code, 1)
        for a in alerts[-10:]:
            color = red if a["severity"].strip() == "CRITICAL" else yellow
            print(f"    {dim(a['ts'])}  {color(a['severity'])}  {a['key']}")
            print(f"      {a['message'][:120]}")

    print()
    print(bold("=" * 70))
    print()
    return exit_code


if __name__ == "__main__":
    sys.exit(render())
