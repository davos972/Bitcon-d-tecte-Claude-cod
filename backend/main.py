import asyncio
import json
import logging
import os
import time
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Load backend/.env so CRYPTOCOMPARE_API_KEY (and friends) are available via
# os.getenv below. Without this, the key in .env is silently ignored and the
# app falls back to Coinbase even when a key is configured.
load_dotenv()

# Logging MUST be configured at the very top, before any function definition.
# Bug lesson #4: if logger is used inside an except block but defined later,
# the app crashes in production (rate-limit → except → NameError → 502).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="BTC Markov Predictor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # allow_credentials must be False when allow_origins is "*": browsers reject
    # the wildcard + credentials combination. We send no cookies/auth anyway.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")
CC_BASE = "https://min-api.cryptocompare.com/data"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Binance is geo-blocked (451) from many servers → never use it as a server source.
# Coinbase Exchange passes everywhere.


def _cc_headers() -> dict:
    if CRYPTOCOMPARE_API_KEY:
        return {"authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}"}
    return {}


def _cc_error_message(data: dict) -> Optional[str]:
    """
    Detect a CryptoCompare error body, returning its message (else None).

    CryptoCompare/CoinDesk returns errors in several shapes — all HTTP 200 or
    401 with a JSON body that must be inspected:
      - legacy rate-limit:  {"Response": "Error", "Message": "..."}
      - current API-key/quota: {"Data": {}, "Err": {"message": "...", ...}}
    The endpoints now require an API key (CRYPTOCOMPARE_API_KEY); without one
    the body uses the "Err" shape. We must catch both so we degrade to Coinbase
    cleanly instead of treating an error body as empty data.
    """
    if not isinstance(data, dict):
        return "non-dict response"
    if data.get("Response") == "Error":
        return data.get("Message") or "Response=Error"
    err = data.get("Err")
    if isinstance(err, dict) and err:
        return err.get("message") or "Err"
    return None


# ---------------------------------------------------------------------------
# Price endpoints
# ---------------------------------------------------------------------------

async def _fetch_price_cc() -> Optional[float]:
    url = f"{CC_BASE}/price?fsym=BTC&tsyms=USD"
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(url, headers=_cc_headers())
            data = r.json()
            # CryptoCompare returns HTTP 200/401 with an error body, not a clean
            # status — must inspect the body (both legacy and new "Err" shapes).
            err = _cc_error_message(data)
            if err:
                logger.warning("CryptoCompare price error: %s", err)
                return None
            price = data.get("USD")
            if isinstance(price, (int, float)):
                return float(price)
    except Exception as exc:
        logger.error("CryptoCompare price exception: %s", exc)
    return None


async def _fetch_price_coinbase() -> Optional[float]:
    url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(url)
            data = r.json()
            return float(data["data"]["amount"])
    except Exception as exc:
        logger.error("Coinbase price exception: %s", exc)
    return None


@app.get("/api/price")
async def api_price():
    price = await _fetch_price_cc()
    source = "cryptocompare"
    if price is None:
        price = await _fetch_price_coinbase()
        source = "coinbase"
    if price is None:
        raise HTTPException(status_code=503, detail="Price unavailable from all sources")
    return {"price": price, "source": source, "ts": int(time.time())}


# ---------------------------------------------------------------------------
# Candle helpers
# ---------------------------------------------------------------------------

_CANDLE_CFG = {
    "5M":  {"endpoint": "v2/histominute", "aggregate": 5},
    "15M": {"endpoint": "v2/histominute", "aggregate": 15},
    "1H":  {"endpoint": "v2/histohour",   "aggregate": 1},
}

# Cycle length in seconds per mode — the UTC grid candles must align to.
CYCLE_SECONDS = {"5M": 300, "15M": 900, "1H": 3600}
_CB_GRANULARITY = CYCLE_SECONDS

# How many candles we aim to load for reliable Markov frequencies.
TARGET_CANDLES = 2000
# Coinbase returns at most ~300 candles per request → paginate this many pages.
_CB_MAX_PAGES = 7

# Server-side cache: many clients each poll 3 timeframes every ~12s, and each
# call pulls ~2000 candles upstream. Without this, upstream rate-limits fast.
_CANDLE_CACHE: dict = {}            # mode -> (fetched_at, source, candles)
_CANDLE_CACHE_TTL = 10.0            # seconds
_CANDLE_LOCKS: dict = {}           # mode -> asyncio.Lock (collapse concurrent fetches)


def _normalize_candle_time(t: int, mode: str) -> int:
    """
    Snap a candle timestamp onto the UTC cycle grid (:00/:05, :00/:15, :00).

    The freshness check and the Price-to-Beat exact match both compare candle
    times to UTC window boundaries with strict equality. Coinbase candles are
    already grid-aligned, but CryptoCompare's *aggregated* candles are not
    guaranteed to be — their grid depends on the request time. Normalizing here
    makes both sources behave identically, so the client never gets stuck on a
    freshness mismatch (which would freeze the screen on "UPDATING…").
    """
    cycle = CYCLE_SECONDS[mode]
    return (int(t) // cycle) * cycle


def _normalize_candles(candles: list, mode: str) -> list:
    """Normalize times to the UTC grid and de-duplicate (keep last per slot)."""
    by_slot: dict = {}
    for c in candles:
        c = {**c, "time": _normalize_candle_time(c["time"], mode)}
        by_slot[c["time"]] = c
    return sorted(by_slot.values(), key=lambda x: x["time"])


async def _fetch_candles_cc(mode: str, limit: int = TARGET_CANDLES) -> Optional[list]:
    cfg = _CANDLE_CFG[mode]
    url = f"{CC_BASE}/{cfg['endpoint']}"
    params = {
        "fsym": "BTC",
        "tsym": "USD",
        "limit": limit,
        "aggregate": cfg["aggregate"],
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(url, params=params, headers=_cc_headers())
            data = r.json()
            err = _cc_error_message(data)
            if err:
                logger.warning("CryptoCompare candles error [%s]: %s", mode, err)
                return None
            candles = data.get("Data", {}).get("Data", [])
            return candles if candles else None
    except Exception as exc:
        logger.error("CryptoCompare candles exception [%s]: %s", mode, exc)
        return None


async def _fetch_candles_coinbase(mode: str) -> Optional[list]:
    """
    Coinbase Exchange caps each request at ~300 candles. Paginate backwards via
    start/end so we approach TARGET_CANDLES — with ~300 candles a 3-step Markov
    pattern rarely gets enough occurrences and collapses to N=1 (weak signal).
    """
    gran = _CB_GRANULARITY[mode]
    url = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
    collected: dict = {}
    end = int(time.time())
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            for _ in range(_CB_MAX_PAGES):
                start = end - gran * 300
                params = {"granularity": gran, "start": start, "end": end}
                r = await c.get(url, params=params)
                raw = r.json()
                if not isinstance(raw, list) or not raw:
                    break
                # Coinbase format: [time, low, high, open, close, volume]
                for row in raw:
                    collected[row[0]] = {
                        "time": row[0], "low": row[1], "high": row[2],
                        "open": row[3], "close": row[4],
                    }
                # Walk further back from the oldest candle we just received.
                oldest = min(row[0] for row in raw)
                end = oldest - gran
                if len(collected) >= TARGET_CANDLES:
                    break
        candles = sorted(collected.values(), key=lambda x: x["time"])
        return candles if candles else None
    except Exception as exc:
        logger.error("Coinbase candles exception [%s]: %s", mode, exc)
        return None


async def _get_candles(mode: str) -> tuple[Optional[list], str]:
    """
    Cached, normalized candle accessor. Returns (candles, source).

    A short TTL cache shared across clients + a per-mode lock so that
    concurrent requests for the same timeframe trigger a single upstream fetch.
    """
    now = time.time()
    cached = _CANDLE_CACHE.get(mode)
    if cached and now - cached[0] < _CANDLE_CACHE_TTL:
        return cached[2], cached[1]

    lock = _CANDLE_LOCKS.setdefault(mode, asyncio.Lock())
    async with lock:
        # Re-check: another coroutine may have refreshed while we waited.
        cached = _CANDLE_CACHE.get(mode)
        if cached and time.time() - cached[0] < _CANDLE_CACHE_TTL:
            return cached[2], cached[1]

        # Pick the source with the most history — more candles => more reliable
        # Markov frequencies (guide target ~2000). CryptoCompare's `limit` caps
        # at 2000 *base minutes*, so an aggregated 15M call yields only ~133
        # candles; Coinbase paginates to ~2000. CryptoCompare wins only for 1H
        # (histohour returns 2000 hourly candles in one call). So: take CC's one
        # cheap call, and only pay for Coinbase pagination when CC came back thin.
        cc = await _fetch_candles_cc(mode)
        if cc and len(cc) >= TARGET_CANDLES * 0.8:
            candles, source = cc, "cryptocompare"
        else:
            cb = await _fetch_candles_coinbase(mode)
            if cb and (not cc or len(cb) > len(cc)):
                candles, source = cb, "coinbase"
            elif cc:
                candles, source = cc, "cryptocompare"
            else:
                candles, source = None, "none"

        if not candles:
            # Serve stale cache rather than a hard failure, if we have any.
            if cached:
                logger.warning("Candles unavailable [%s] — serving stale cache", mode)
                return cached[2], cached[1]
            return None, "none"

        candles = _normalize_candles(candles, mode)
        _CANDLE_CACHE[mode] = (time.time(), source, candles)
        return candles, source


# ---------------------------------------------------------------------------
# Markov engine
# ---------------------------------------------------------------------------

def _classify(candle: dict) -> str:
    """U if close >= open. The >= is crucial: Polymarket rule is tie = Up."""
    return "U" if float(candle["close"]) >= float(candle["open"]) else "D"


def _compute_markov(closed: list, max_n: int = 3) -> Optional[dict]:
    if len(closed) < max_n + 1:
        return None

    directions = [_classify(c) for c in closed]
    last_closed_time = int(closed[-1]["time"])

    # Try N=3, fall back to N=2 then N=1 if fewer than 20 occurrences.
    for n in range(max_n, 0, -1):
        if len(directions) < n + 1:
            continue
        current_pattern = tuple(directions[-n:])
        up_count = 0
        down_count = 0
        for i in range(len(directions) - n):
            if tuple(directions[i: i + n]) == current_pattern:
                if directions[i + n] == "U":
                    up_count += 1
                else:
                    down_count += 1
        total = up_count + down_count
        if total < 20:
            continue
        prob_up = up_count / total
        # Confidence: HIGH ≥50 matches, MEDIUM 20-49
        confidence = "HIGH" if total >= 50 else "MEDIUM"
        return {
            "prob_up": round(prob_up, 4),
            "prob_down": round(1 - prob_up, 4),
            "direction": "UP" if prob_up >= 0.5 else "DOWN",
            "pattern": list(current_pattern),
            "up_count": up_count,
            "down_count": down_count,
            "sample_size": total,
            "n_used": n,
            "confidence": confidence,
            "candle_count": len(closed),
            "last_closed_time": last_closed_time,
        }

    # Insufficient sample for any N → return LOW confidence, no direction
    pattern = list(directions[-max_n:]) if len(directions) >= max_n else list(directions)
    return {
        "prob_up": 0.5,
        "prob_down": 0.5,
        "direction": "NONE",
        "pattern": pattern,
        "up_count": 0,
        "down_count": 0,
        "sample_size": 0,
        "n_used": max_n,
        "confidence": "LOW",
        "candle_count": len(closed),
        "last_closed_time": last_closed_time if closed else 0,
    }


@app.get("/api/markov")
async def api_markov(
    mode: str = Query("15M", pattern="^(5M|15M|1H)$"),
    window: int = Query(..., description="Current window start timestamp (UTC seconds)"),
):
    candles, candle_source = await _get_candles(mode)
    if not candles:
        raise HTTPException(status_code=503, detail="No candle data available")

    # Align the requested window to the UTC grid too, so client/server agree
    # even if the client clock is slightly off.
    window = _normalize_candle_time(window, mode)

    # Exclude the candle currently forming (time >= window_start).
    # The client passes its window_start so both sides are aligned. Candle
    # times are already normalized to the UTC grid in _get_candles.
    closed = [c for c in candles if int(c["time"]) < window]

    # Find open price of current window candle (for Price to Beat refinement)
    current_window_open: Optional[float] = None
    for c in candles:
        if int(c["time"]) == window:
            current_window_open = float(c["open"])
            break

    result = _compute_markov(closed)
    if result is None:
        raise HTTPException(status_code=503, detail="Insufficient closed candle data")

    # Last 20 closed candle directions for the candle strip display
    recent_dirs = [_classify(c) for c in closed[-20:]]

    result["candle_source"] = candle_source
    result["current_window_open"] = current_window_open
    result["recent_candles"] = recent_dirs
    return result


# ---------------------------------------------------------------------------
# Polymarket resolution proxy
# ---------------------------------------------------------------------------

def _parse_json_field(raw):
    """Outcomes/prices sometimes arrive as JSON strings, sometimes arrays."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return raw if isinstance(raw, list) else []


# A market is only considered resolved once the winning outcome trades at/above
# this price. Intermediate prices mean the market is still live.
WINNER_THRESHOLD = 0.9


def _resolve_market(market: Optional[dict]) -> Optional[str]:
    """
    Pure resolution logic, extracted for testing. Returns the winning outcome
    string (e.g. "Up"/"Down") or None if not resolved / not found.
    """
    if not isinstance(market, dict) or not market:
        return None
    outcomes = _parse_json_field(market.get("outcomes", []))
    prices = _parse_json_field(market.get("outcomePrices", []))
    for outcome, price_raw in zip(outcomes, prices):
        try:
            p = float(price_raw)
        except (ValueError, TypeError):
            continue
        if p >= WINNER_THRESHOLD:
            return str(outcome)
    return None


@app.get("/api/poly_resolution")
async def api_poly_resolution(slug: str = Query(...)):
    """
    Proxy to Gamma API. Returns resolved winner for a given market slug.
    Slug format: btc-updown-5m-{ts} or btc-updown-15m-{ts}
    No 1H market exists on Polymarket → frontend handles that locally.
    """
    url = f"{GAMMA_API_BASE}/markets"
    # Markets resolved by default are hidden → must pass closed=true
    params = {"slug": slug, "closed": "true"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, params=params)
            data = r.json()
    except Exception as exc:
        logger.error("Gamma API exception: %s", exc)
        raise HTTPException(status_code=503, detail="Gamma API unavailable")

    markets = data if isinstance(data, list) else [data]
    market = markets[0] if markets else None
    winner = _resolve_market(market)

    return {
        "resolved": winner is not None,
        "winner": winner,
        "slug": slug,
        "market_id": market.get("id") if isinstance(market, dict) else None,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "ts": int(time.time())}
