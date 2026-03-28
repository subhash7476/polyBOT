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
        *,
        question: str | None = None,
        strategy_type: str = "directional",
        category: str = "unknown",
    ):
        """Log every evaluated signal, whether or not it becomes a completed trade."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "token_id": token_id,
            "question": question,
            "model_prob": round(model_prob, 4),
            "market_prob": round(market_prob, 4),
            "edge": round(model_prob - market_prob, 4),
            "ev": round(ev, 4),
            "size_usdc": round(size_usdc, 2),
            "side": side,
            "strategy_type": strategy_type,
            "category": category,
            "signals": signal_summary,
            "outcome": None,
            "resolved_at": None,
        }
        with self.log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def record_outcome(
        self,
        token_id: str,
        resolved_yes: bool,
        *,
        resolved_at: str | None = None,
    ) -> bool:
        """Update matching fill records with actual resolution outcome exactly once."""
        if not self.log_file.exists():
            return False

        lines = self.log_file.read_text(encoding="utf-8").splitlines()
        updated = []
        wrote = False
        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["token_id"] == token_id and record["outcome"] is None:
                record["outcome"] = 1 if resolved_yes else 0
                record["resolved_at"] = resolved_at or datetime.now(timezone.utc).isoformat()
                wrote = True
            updated.append(json.dumps(record))
        self.log_file.write_text("\n".join(updated) + "\n", encoding="utf-8")
        return wrote
