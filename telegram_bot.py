"""
telegram_bot.py — Remote control for the Polymarket bot via Telegram.

Run as a separate process (NOT part of main.py):
    python telegram_bot.py

Commands:
    /status    — show bot process status
    /balance   — show current USDC balance (reads bot.pid + state indirectly)
    /redeemall — trigger batch redemption subprocess
    /stop      — send SIGTERM to bot process
    /restart   — stop + restart bot process
    /help      — list commands

Setup: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""
import html
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PID_FILE   = Path(__file__).parent / "bot.pid"

_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_authorized(chat_id: int, allowed_id: str) -> bool:
    """Return True only if the message comes from the configured chat ID."""
    return bool(allowed_id) and str(chat_id) == allowed_id


def sanitize_output(text: str) -> str:
    """Escape HTML special characters so Telegram doesn't choke on bot output."""
    return html.escape(str(text))


def send_message(chat_id: int, text: str) -> None:
    try:
        requests.post(
            f"{_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        print(f"[send_message] failed: {exc}", file=sys.stderr)


def get_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_status(chat_id: int) -> None:
    pid = get_pid()
    running = is_running(pid)
    status = "RUNNING" if running else "STOPPED"
    send_message(chat_id, f"Bot: <b>{status}</b> (pid={pid})")


def handle_balance(chat_id: int) -> None:
    send_message(chat_id, "Fetching balance...")
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "import asyncio, httpx, os; "
             "r = asyncio.run(httpx.AsyncClient().get("
             "'https://data-api.polymarket.com/value',"
             " params={'user': os.getenv('FUNDER_ADDRESS', '')})); "
             "print(r.json())"],
            capture_output=True, text=True, timeout=30,
        )
        out = sanitize_output((result.stdout or result.stderr).strip()[:1000])
        send_message(chat_id, out or "No data.")
    except subprocess.TimeoutExpired:
        send_message(chat_id, "balance fetch timed out.")
    except Exception as exc:
        send_message(chat_id, f"Error: {sanitize_output(str(exc))}")


def handle_redeemall(chat_id: int) -> None:
    send_message(chat_id, "Starting redeemall...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "trading.redeemall"],
            capture_output=True, text=True, timeout=300,
            cwd=str(Path(__file__).parent),
        )
        out = sanitize_output((result.stdout + result.stderr).strip()[-1000:])
        send_message(chat_id, out or "Done.")
    except subprocess.TimeoutExpired:
        send_message(chat_id, "redeemall timed out (>5 min).")
    except Exception as exc:
        send_message(chat_id, f"Error: {sanitize_output(str(exc))}")


def _force_kill(pid: int) -> None:
    """Force-kill a process in a cross-platform way."""
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def handle_stop(chat_id: int) -> None:
    pid = get_pid()
    if not is_running(pid):
        send_message(chat_id, "Bot not running.")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except PermissionError:
        send_message(chat_id, f"Permission denied sending signal to pid={pid}.")
        return
    time.sleep(3)
    if is_running(pid):
        _force_kill(pid)
    send_message(chat_id, f"Stopped (pid={pid}).")


def handle_restart(chat_id: int) -> None:
    handle_stop(chat_id)
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "main.py")],
        cwd=str(Path(__file__).parent),
    )
    PID_FILE.write_text(str(proc.pid))
    send_message(chat_id, f"Restarted (pid={proc.pid}).")


def handle_help(chat_id: int) -> None:
    send_message(
        chat_id,
        "/status — process status\n"
        "/balance — USDC balance\n"
        "/redeemall — redeem winnings\n"
        "/stop — stop bot\n"
        "/restart — restart bot\n"
        "/help — this message",
    )


HANDLERS = {
    "/status":    handle_status,
    "/balance":   handle_balance,
    "/redeemall": handle_redeemall,
    "/stop":      handle_stop,
    "/restart":   handle_restart,
    "/help":      handle_help,
}


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

def poll_forever() -> None:
    offset = 0
    print(f"Telegram bot starting (allowed_id={ALLOWED_ID})")
    while True:
        try:
            resp = requests.get(
                f"{_BASE}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            updates = resp.json().get("result", [])
        except Exception as exc:
            print(f"[poll] error: {exc}", file=sys.stderr)
            time.sleep(5)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            text    = (msg.get("text") or "").strip()

            if not chat_id or not text:
                continue
            if not is_authorized(chat_id, ALLOWED_ID):
                send_message(chat_id, "Unauthorized.")
                continue

            # Match command (strip @botname suffix if present)
            cmd = text.split()[0].split("@")[0].lower()
            handler = HANDLERS.get(cmd)
            if handler:
                try:
                    handler(chat_id)
                except Exception as exc:
                    send_message(chat_id, f"Error: {sanitize_output(str(exc))}")
            else:
                send_message(chat_id, f"Unknown command. Try /help")


def main() -> None:
    if not BOT_TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN not set in .env")
    poll_forever()


if __name__ == "__main__":
    main()
