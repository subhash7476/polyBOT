import json
from datetime import datetime, timezone
from pathlib import Path
from utils.logger import get_logger

log = get_logger(__name__)


class CalibrationTracker:
    def __init__(self, log_file: str = "fills.jsonl"):
        self.log_file = Path(log_file)

    def log_signal(
        self,
        token_id: str,
        model_prob: float,
        market_prob: float,
        signal_summary: dict,
        size_usdc: float,
        ev: float,
        side: str = "BUY_YES",
    ):
        """Log every evaluated signal — both taken and skipped."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "token_id": token_id,
            "model_prob": round(model_prob, 4),
            "market_prob": round(market_prob, 4),
            "edge": round(model_prob - market_prob, 4),
            "ev": round(ev, 4),
            "size_usdc": round(size_usdc, 2),
            "side": side,
            "signals": signal_summary,
            "outcome": None,   # filled at resolution by record_outcome()
        }
        with self.log_file.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def record_outcome(self, token_id: str, resolved_yes: bool):
        """Update matching fill records with actual resolution outcome."""
        if not self.log_file.exists():
            return
        lines = self.log_file.read_text().splitlines()
        updated = []
        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["token_id"] == token_id and record["outcome"] is None:
                record["outcome"] = 1 if resolved_yes else 0
            updated.append(json.dumps(record))
        self.log_file.write_text("\n".join(updated) + "\n")
