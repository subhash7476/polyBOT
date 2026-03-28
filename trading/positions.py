from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


OPEN = "open"
RESOLVED_PENDING_REDEEM = "resolved_pending_redeem"
REDEEMED = "redeemed"
CLOSED_PAPER = "closed_paper"


@dataclass
class TrackedPosition:
    token_id: str
    no_token_id: str
    question: str
    category: str
    group_key: str
    side: str
    size_usdc: float
    entry_price: float
    market_price_at_open: float
    condition_id: str = ""
    strategy_type: str = "directional"
    status: str = OPEN
    opened_at: float = field(default_factory=time.time)
    resolved_yes: bool | None = None
    resolved_at: float | None = None
    redeemed_at: float | None = None

    @property
    def is_active(self) -> bool:
        return self.status == OPEN

    @property
    def is_pending_redeem(self) -> bool:
        return self.status == RESOLVED_PENDING_REDEEM

    @property
    def shares(self) -> float:
        return self.size_usdc / max(self.entry_price, 0.0001)

    def to_record(self) -> dict:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict) -> "TrackedPosition":
        return cls(**record)


class PositionLedger:
    def __init__(self, path: str = "positions.jsonl"):
        self.path = Path(path)

    def append(self, position: TrackedPosition) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(position.to_record()) + "\n")

    def load_latest(self) -> dict[str, TrackedPosition]:
        latest: dict[str, TrackedPosition] = {}
        if not self.path.exists():
            return latest
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            pos = TrackedPosition.from_record(record)
            latest[pos.token_id] = pos
        return latest

    def load_active(self) -> tuple[dict[str, TrackedPosition], dict[str, TrackedPosition]]:
        open_positions: dict[str, TrackedPosition] = {}
        pending_redemptions: dict[str, TrackedPosition] = {}
        for token_id, pos in self.load_latest().items():
            if pos.status == OPEN:
                open_positions[token_id] = pos
            elif pos.status == RESOLVED_PENDING_REDEEM:
                pending_redemptions[token_id] = pos
        return open_positions, pending_redemptions

    def iter_records(self) -> Iterable[TrackedPosition]:
        for pos in self.load_latest().values():
            yield pos
