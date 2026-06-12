import json
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

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
    allow_credentials=True,
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


# ---------------------------------------------------------------------------
# Price endpoints
# ---------------------------------------------------------------------------

async def _fetch_price_cc() -> Optional[float]:
    url = f"{CC_BASE}/price?fsym=BTC&tsyms=USD"
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(url, headers=_cc_headers())
            data = r.json()
            # CryptoCompare returns HTTP 200 with Response:"Error" on rate-limit.
            # Must check the body, not just the status code.
            if data.get("Response") == "Error":
                logger.warning("CryptoCompare price error: %s", data.get("Message", ""))
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

_CB_GRANULARITY = {"5M": 300, "15M": 900, "1H": 3600}


async def _fetch_candles_cc(mode: str, limit: int = 2000) -> Optional[list]:
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
            if data.get("Response") == "Error":
                logger.warning("CryptoCompare candles error [%s]: %s", mode, data.get("Message", ""))
                return None
            candles = data.get("Data", {}).get("Data", [])
            return candles if candles else None
    except Exception as exc:
        logger.error("CryptoCompare candles exception [%s]: %s", mode, exc)
        return None


async def _fetch_candles_coinbase(mode: str) -> Optional[list]:
    gran = _CB_GRANULARITY[mode]
    url = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
    params = {"granularity": gran}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, params=params)
            raw = r.json()
            if not isinstance(raw, list):
                return None
            # Coinbase format: [time, low, high, open, close, volume]
            candles = sorted(
                [{"time": row[0], "low": row[1], "high": row[2], "open": row[3], "close": row[4]}
                 for row in raw],
                key=lambda x: x["time"],
            )
            return candles if candles else None
    except Exception as exc:
        logger.error("Coinbase candles exception [%s]: %s", mode, exc)
        return None


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
    candles = await _fetch_candles_cc(mode)
    candle_source = "cryptocompare"
    if not candles:
        candles = await _fetch_candles_coinbase(mode)
        candle_source = "coinbase"
    if not candles:
        raise HTTPException(status_code=503, detail="No candle data available")

    # Exclude the candle currently forming (time >= window_start).
    # The client passes its window_start so both sides are aligned.
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
    if not markets or not markets[0]:
        return {"resolved": False, "winner": None, "slug": slug}

    market = markets[0]

    def _parse_field(raw):
        """Outcomes/prices sometimes arrive as JSON strings, sometimes arrays."""
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return []
        return raw if isinstance(raw, list) else []

    outcomes = _parse_field(market.get("outcomes", []))
    prices = _parse_field(market.get("outcomePrices", []))

    winner = None
    for outcome, price_raw in zip(outcomes, prices):
        try:
            p = float(price_raw)
        except (ValueError, TypeError):
            continue
        # Require ≥0.9 before declaring winner; intermediate prices mean still live
        if p >= 0.9:
            winner = str(outcome)
            break

    return {
        "resolved": winner is not None,
        "winner": winner,
        "slug": slug,
        "market_id": market.get("id"),
    }


@app.get("/health")
async def health():
    return {"status": "ok", "ts": int(time.time())}
