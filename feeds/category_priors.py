"""Optional external priors for sports, politics, and event markets.

This feed writes category-level priors into FeedState.category_priors so the
router can consume them without hard-coding each data source into the quote path.

Supported sources:
  - Sports: Sportradar probabilities API (optional API key)
  - Politics: OpenFEC candidate totals / committee receipts
  - Events: SEC EDGAR submissions / filing recency

All sources are optional. If credentials or mappings are missing, the feed degrades
to a no-op for that market rather than failing the bot.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

import config
from market.state import AppState, ContractState
from utils.logger import get_logger

log = get_logger(__name__)

_POLL_INTERVAL = 900
_USER_AGENT = "Mozilla/5.0 (compatible; polymarket-bot/1.0)"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _question_key(question: str) -> str:
    return _norm(question)


def _extract_entities(question: str, max_entities: int = 2) -> list[str]:
    """Crude proper-noun extraction for politics/event markets."""
    tokens = re.findall(r"[A-Z][A-Za-z&.'-]+(?:\s+[A-Z][A-Za-z&.'-]+)*", question)
    stop = {
        "Will", "What", "When", "Where", "Why", "How", "The", "A", "An", "In",
        "By", "On", "Of", "For", "To", "At", "Is", "Are", "Do", "Does", "Did",
    }
    entities = []
    for tok in tokens:
        if tok.split()[0] in stop:
            continue
        if len(tok) < 3:
            continue
        entities.append(tok.strip())
        if len(entities) >= max_entities:
            break
    return entities


def _set_prior(
    category_priors: dict,
    category: str,
    key: str,
    prob: float,
    confidence: float,
    source: str,
) -> None:
    category_priors.setdefault(category, {})[_norm(key)] = {
        "prob": max(0.01, min(0.99, prob)),
        "confidence": max(0.0, min(1.0, confidence)),
        "source": source,
    }


def _extract_yes_no_prob(prob_json: dict) -> tuple[float | None, float]:
    """Best-effort extraction of a binary home/away or yes/no probability."""
    markets = prob_json.get("markets") or prob_json.get("market") or []
    if isinstance(markets, dict):
        markets = [markets]
    for market in markets:
        outcomes = market.get("outcomes") or market.get("outcome") or []
        if isinstance(outcomes, dict):
            outcomes = [outcomes]
        if not outcomes:
            continue
        probs = []
        for out in outcomes[:2]:
            p = out.get("probability")
            if p is None:
                p = out.get("probabilities")
            if p is None:
                continue
            try:
                probs.append(float(p) / 100.0 if float(p) > 1.0 else float(p))
            except (TypeError, ValueError):
                continue
        if probs:
            return probs[0], min(1.0, 0.5 + abs(probs[0] - 0.5))
    return None, 0.0


@dataclass
class _PolityCandidate:
    candidate_id: str
    name: str
    total_receipts: float
    total_disbursements: float


class CategoryPriorFeed:
    def __init__(self, state: AppState):
        self._state = state
        self._fec_cache: dict[str, tuple[float, float, float]] = {}
        self._sec_ticker_to_cik: dict[str, str] | None = None

    async def start(self) -> None:
        log.info("starting")
        async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=20.0) as client:
            while True:
                try:
                    await self._refresh(client)
                except Exception as exc:
                    log.warning(f"category priors refresh failed: {exc}")
                await asyncio.sleep(_POLL_INTERVAL)

    async def _refresh(self, client: httpx.AsyncClient) -> None:
        async with self._state._lock:
            markets = list(self._state.markets.values())

        priors: dict = {}
        await self._refresh_sports(client, markets, priors)
        await self._refresh_politics(client, markets, priors)
        await self._refresh_events(client, markets, priors)

        async with self._state._lock:
            self._state.feeds.category_priors = priors
        self._state.stamp_feed("category_priors")
        if priors:
            log.debug(
                "category priors updated: "
                + ", ".join(f"{cat}={len(items)}" for cat, items in priors.items())
            )

    async def _refresh_sports(
        self,
        client: httpx.AsyncClient,
        markets: list[ContractState],
        priors: dict,
    ) -> None:
        api_key = (config.SPORTRADAR_API_KEY or "").strip()
        if not api_key:
            return

        async def _fetch_schedule(url: str, headers: dict) -> list[dict]:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if isinstance(data, dict):
                for key in ("games", "sport_events", "events", "results"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                return []
            return data if isinstance(data, list) else []

        for cs in markets:
            if cs.category != "sports":
                continue
            q = _norm(cs.question)
            teams = _extract_entities(cs.question, max_entities=2)
            if len(teams) < 1:
                continue

            league = None
            if any(x in q for x in ("lakers", "warriors", "celtics", "knicks", "nba", "heat", "bucks", "suns", "mavericks")):
                league = "nba"
            elif any(x in q for x in ("yankees", "dodgers", "braves", "astros", "phillies", "mets", "mlb")):
                league = "mlb"
            elif any(x in q for x in ("soccer", "premier league", "champions league", "liverpool", "arsenal", "chelsea", "madrid", "barcelona", "bayern", "psg")):
                league = "soccer"
            if league is None:
                continue

            target_date = datetime.now(timezone.utc).date()
            if cs.end_date_iso:
                try:
                    target_date = datetime.fromisoformat(cs.end_date_iso.replace("Z", "+00:00")).date()
                except ValueError:
                    pass

            schedule_urls = []
            headers = {"x-api-key": api_key, "User-Agent": _USER_AGENT}
            if league == "nba":
                for offset in (-1, 0, 1):
                    d = target_date + timedelta(days=offset)
                    schedule_urls.append(
                        f"https://api.sportradar.com/nba/trial/v4/en/games/{d.year}/{d.month:02d}/{d.day:02d}/schedule.json"
                    )
            elif league == "mlb":
                for offset in (-1, 0, 1):
                    d = target_date + timedelta(days=offset)
                    schedule_urls.append(
                        f"https://api.sportradar.com/mlb/trial/v7/en/games/{d.year}/{d.month:02d}/{d.day:02d}/schedule.json"
                    )
            else:  # soccer
                for offset in (-1, 0, 1):
                    d = target_date + timedelta(days=offset)
                    schedule_urls.append(
                        f"https://api.sportradar.com/soccer/trial/v4/en/schedules/{d.isoformat()}/schedules.json"
                    )

            question_l = q
            for url in schedule_urls:
                try:
                    games = await _fetch_schedule(url, headers)
                except Exception:
                    continue
                for game in games:
                    if league == "soccer":
                        ev = game.get("sport_event") or game
                        context = ev.get("sport_event_context", {})
                        comps = ev.get("competitors") or []
                        names = " ".join(
                            str(c.get("name", "")) for c in comps if isinstance(c, dict)
                        ).lower()
                        sr_id = ev.get("id") or ev.get("sport_event", {}).get("id")
                    else:
                        home = game.get("home") or {}
                        away = game.get("away") or {}
                        names = f"{home.get('name','')} {away.get('name','')}".lower()
                        sr_id = game.get("sr_id") or game.get("id")

                    if not sr_id or not names:
                        continue
                    if not any(_norm(team) in names or _norm(team) in question_l for team in teams):
                        continue

                    prob_urls = {
                        "nba": f"https://api.sportradar.com/probabilities/trial/v1/en/sport_events/{sr_id}/probabilities.json",
                        "mlb": f"https://api.sportradar.com/baseball-probabilities/trial/v2/en/sport_events/{sr_id}/sport_event_probabilities.json",
                        "soccer": f"https://api.sportradar.com/soccer-probabilities/trial/v4/en/sport_events/{sr_id}/sport_event_probabilities.json",
                    }
                    try:
                        resp = await client.get(prob_urls[league], headers=headers)
                        if resp.status_code != 200:
                            continue
                        prob_json = resp.json()
                        prob, confidence = _extract_yes_no_prob(prob_json)
                        if prob is None:
                            continue
                        _set_prior(priors, "sports", _question_key(cs.question), prob, max(0.25, confidence), f"sportradar_{league}")
                        break
                    except Exception:
                        continue
                if _question_key(cs.question) in priors.get("sports", {}):
                    break

    async def _search_fec_candidate(self, client: httpx.AsyncClient, name: str) -> list[_PolityCandidate]:
        api_key = (config.FEC_API_KEY or "").strip()
        if not api_key:
            return []
        resp = await client.get(
            "https://api.open.fec.gov/v1/candidates/search/",
            params={"q": name, "api_key": api_key, "per_page": 5},
        )
        resp.raise_for_status()
        results = []
        for row in resp.json().get("results", []):
            cid = row.get("candidate_id")
            cname = row.get("name", "")
            if not cid or not cname:
                continue
            totals = await client.get(
                f"https://api.open.fec.gov/v1/candidate/{cid}/totals/",
                params={"api_key": api_key},
            )
            if totals.status_code != 200:
                continue
            total_row = (totals.json().get("results") or [{}])[0]
            results.append(_PolityCandidate(
                candidate_id=cid,
                name=cname,
                total_receipts=float(total_row.get("receipts", 0.0) or 0.0),
                total_disbursements=float(total_row.get("disbursements", 0.0) or 0.0),
            ))
        return results

    async def _refresh_politics(
        self,
        client: httpx.AsyncClient,
        markets: list[ContractState],
        priors: dict,
    ) -> None:
        api_key = (config.FEC_API_KEY or "").strip()
        if not api_key:
            return

        for cs in markets:
            if cs.category not in ("politics", "election"):
                continue
            entities = _extract_entities(cs.question, max_entities=2)
            if not entities:
                continue

            # Use the leading candidate-like entities from the question to find
            # candidate totals. If we only have one side, we still emit a weak prior.
            candidates: list[_PolityCandidate] = []
            for ent in entities[:2]:
                candidates.extend(await self._search_fec_candidate(client, ent))

            if not candidates:
                continue

            # Prefer the candidate with the stronger recent receipts cadence.
            candidates.sort(key=lambda c: (c.total_receipts - c.total_disbursements), reverse=True)
            top = candidates[0]
            second = candidates[1] if len(candidates) > 1 else None

            if second is None:
                prob = 0.55
                conf = 0.15
            else:
                diff = top.total_receipts - second.total_receipts
                scale = max(top.total_receipts + second.total_receipts, 1.0)
                prob = 0.5 + max(-0.15, min(0.15, diff / scale * 0.3))
                conf = 0.35

            _set_prior(priors, "politics", _question_key(cs.question), prob, conf, "openfec")

    async def _load_sec_ticker_map(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self._sec_ticker_to_cik is not None:
            return self._sec_ticker_to_cik
        resp = await client.get("https://www.sec.gov/files/company_tickers.json")
        resp.raise_for_status()
        raw = resp.json()
        mapping: dict[str, str] = {}
        for row in raw.values():
            ticker = str(row.get("ticker", "")).upper()
            cik = str(row.get("cik_str", "")).zfill(10)
            if ticker and cik:
                mapping[ticker] = cik
        self._sec_ticker_to_cik = mapping
        return mapping

    async def _refresh_events(
        self,
        client: httpx.AsyncClient,
        markets: list[ContractState],
        priors: dict,
    ) -> None:
        for cs in markets:
            if cs.category != "event":
                continue
            entities = _extract_entities(cs.question, max_entities=1)
            if not entities:
                continue
            key = _question_key(cs.question)

            # SEC filing recency acts as a directional pressure proxy for company
            # event/announcement markets: recent filing activity tends to increase
            # event likelihood near the market window.
            ticker = entities[0].upper()
            try:
                ticker_map = await self._load_sec_ticker_map(client)
                cik = ticker_map.get(ticker)
                if not cik:
                    continue
                resp = await client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
                resp.raise_for_status()
                recent = resp.json().get("filings", {}).get("recent", {})
                filing_dates = recent.get("filingDate", []) or []
                now = datetime.now(timezone.utc).date()
                count_30d = 0
                for d in filing_dates[:25]:
                    try:
                        fd = datetime.strptime(d, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    if (now - fd).days <= 30:
                        count_30d += 1
                prob = 0.5 + min(0.2, count_30d * 0.02)
                conf = 0.25 if count_30d else 0.10
                _set_prior(priors, "event", key, prob, conf, "sec_edgar")
            except Exception as exc:
                log.debug(f"SEC event prior failed for {ticker}: {exc}")
