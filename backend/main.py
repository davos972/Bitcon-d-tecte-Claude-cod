import asyncio
import json
import logging
import math
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Load backend/.env so CRYPTOCOMPARE_API_KEY (and friends) are available via
# os.getenv below. Resolve the path next to this file so the key loads no matter
# what the current working directory is (e.g. when started from a launcher).
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Logging MUST be configured at the very top, before any function definition.
# Bug lesson #4: if logger is used inside an except block but defined later,
# the app crashes in production (rate-limit → except → NameError → 502).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Server-side autonomous tracker (Option B): record predictions + reconcile
# results 24/7, independent of any open browser/app client. The frontend tracker
# only runs while a tab is open, so history stalls whenever nobody has the app
# up. This background scheduler removes that dependency. Enabled by default; set
# TRACKER_SCHEDULER=0 to fall back to client-only recording.
_SCHEDULER_ENABLED = os.getenv("TRACKER_SCHEDULER", "1") != "0"


@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    """Start/stop the background tracker scheduler with the app lifecycle.

    Uses lifespan (not @app.on_event) on purpose: the test suite instantiates
    TestClient WITHOUT entering its context manager, so lifespan never fires in
    tests → the scheduler makes no network calls during pytest.
    """
    task = None
    if _SCHEDULER_ENABLED:
        task = asyncio.create_task(_scheduler_loop())
        logger.info("Server-side tracker scheduler: started")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("Server-side tracker scheduler: stopped")


app = FastAPI(title="BTC Markov Predictor API", lifespan=_lifespan)

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

# --- Tracker persistence -------------------------------------------------
# The frontend tracker is otherwise per-device (AsyncStorage), so opening the
# app on another device starts from zero. We keep a single shared store on disk
# here so every device reads/writes the same prediction history. JSON file with
# atomic write, serialized by an async lock. Unlimited history + anti-duplicate
# by entry id, per the honesty rules.
# Store directory is configurable so production can point it at a persistent
# disk (set TRACKER_DATA_DIR, e.g. /data on the host). Defaults next to this
# file for local dev.
TRACKER_DATA_DIR = os.getenv(
    "TRACKER_DATA_DIR", os.path.dirname(os.path.abspath(__file__))
)
TRACKER_STORE_PATH = os.path.join(TRACKER_DATA_DIR, "tracker_store.json")
_tracker_lock = asyncio.Lock()

# A scored result must never be downgraded back to PENDING when merging.
_RESULT_RANK = {"PENDING": 0, "EXPIRED": 1, "WIN": 2, "LOSS": 2}

# Reject entries with an implausible windowStart (must be a Unix-seconds
# timestamp after 2020-09-13). Guards the honest tracker against garbage/test
# data — a single bad entry would corrupt the win-rate permanently.
_MIN_WINDOW_START = 1_600_000_000

# Write protection for the shared tracker (the backend is publicly hosted).
# Enforcement is opt-in: only active when TRACKER_API_KEY is configured on the
# server. GET stays open (non-sensitive); POST/DELETE then require the key via
# the X-Api-Key header. NOTE: the frontend ships its copy in the JS bundle, so
# this raises the bar (blocks scanners/casual abuse) but is not bulletproof
# against someone who reads the bundle. The plausibility guard + per-request cap
# below limit the damage a rogue write can do, and DELETE self-heals because
# devices re-push their local history.
TRACKER_API_KEY = os.getenv("TRACKER_API_KEY", "")

# Anti-DoS: reject oversized sync payloads outright.
_MAX_SYNC_ENTRIES = 10_000


def _require_tracker_key(x_api_key: Optional[str]) -> None:
    if not TRACKER_API_KEY:
        return  # not configured → open (local dev)
    if not x_api_key or not secrets.compare_digest(x_api_key, TRACKER_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _is_plausible(e) -> bool:
    if not isinstance(e, dict) or not e.get("id"):
        return False
    ws = e.get("windowStart")
    return isinstance(ws, (int, float)) and not isinstance(ws, bool) and ws >= _MIN_WINDOW_START


def _load_tracker() -> list:
    try:
        with open(TRACKER_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return [e for e in data if _is_plausible(e)]
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.error("Tracker store read failed: %s", exc)
        return []


def _save_tracker(entries: list) -> None:
    tmp = f"{TRACKER_STORE_PATH}.tmp"
    try:
        os.makedirs(os.path.dirname(TRACKER_STORE_PATH) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        os.replace(tmp, TRACKER_STORE_PATH)
    except Exception as exc:
        logger.error("Tracker store write failed: %s", exc)


def _merge_entries(existing: list, incoming: list) -> list:
    """Merge by id. Prefer a scored (non-PENDING) result over PENDING; if both
    are scored keep the existing one (already immutable). Never drops an id."""
    by_id: dict = {}
    for e in existing:
        if _is_plausible(e):
            by_id[e["id"]] = e
    for e in incoming:
        if not _is_plausible(e):
            continue
        cur = by_id.get(e["id"])
        if cur is None:
            by_id[e["id"]] = e
            continue
        cur_rank = _RESULT_RANK.get(cur.get("result", "PENDING"), 0)
        inc_rank = _RESULT_RANK.get(e.get("result", "PENDING"), 0)
        if inc_rank > cur_rank:
            by_id[e["id"]] = e
        elif inc_rank == 0 and cur_rank == 0:
            # both pending — adopt incoming fields (may carry a fresh priceAtClose)
            by_id[e["id"]] = {**cur, **e}
        # else keep existing (already scored / immutable)
    merged = list(by_id.values())
    merged.sort(key=lambda x: (x.get("recordedAt", 0), str(x.get("id", ""))))
    return merged

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


def _markov_core(directions: list, max_n: int = 3) -> Optional[dict]:
    """Pure pattern-frequency step over a U/D sequence, shared by /api/markov
    and /api/backtest so the backtest exercises the EXACT same engine.

    Returns None if the sequence is too short; otherwise a dict with prob_up and
    the matched pattern stats, or a LOW/no-direction dict when no N reaches the
    20-occurrence threshold. Keeps no candle-specific fields (caller adds those).
    """
    if len(directions) < max_n + 1:
        return None

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
        return {
            "prob_up": prob_up,
            "direction": "UP" if prob_up >= 0.5 else "DOWN",
            "pattern": list(current_pattern),
            "up_count": up_count,
            "down_count": down_count,
            "sample_size": total,
            "n_used": n,
            "confidence": "HIGH" if total >= 50 else "MEDIUM",  # by match count
        }

    # Insufficient sample for any N → LOW confidence, no direction.
    return {
        "prob_up": 0.5,
        "direction": "NONE",
        "pattern": list(directions[-max_n:]),
        "up_count": 0,
        "down_count": 0,
        "sample_size": 0,
        "n_used": max_n,
        "confidence": "LOW",
    }


def _compute_markov(closed: list, max_n: int = 3) -> Optional[dict]:
    if len(closed) < max_n + 1:
        return None
    directions = [_classify(c) for c in closed]
    core = _markov_core(directions, max_n)
    prob_up = core["prob_up"]
    return {
        "prob_up": round(prob_up, 4),
        "prob_down": round(1 - prob_up, 4),
        "direction": core["direction"],
        "pattern": core["pattern"],
        "up_count": core["up_count"],
        "down_count": core["down_count"],
        "sample_size": core["sample_size"],
        "n_used": core["n_used"],
        "confidence": core["confidence"],
        "candle_count": len(closed),
        "last_closed_time": int(closed[-1]["time"]),
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
# Walk-forward backtest — replays the SAME engine over history
# ---------------------------------------------------------------------------

def _wilson(wins: int, total: int) -> tuple:
    """95% Wilson score interval for a win rate (returns fractions 0-1)."""
    if total == 0:
        return (0.0, 0.0)
    z = 1.96
    p = wins / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


# Confidence buckets (winning-side prob) — mirrors the live calibration analysis
# so the backtest also shows whether higher confidence is actually more accurate.
_CONF_BINS = [(0.55, 0.575), (0.575, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01)]

# Enough prior candles before we let the engine predict (needs ~50 for HIGH).
_BACKTEST_MIN_HISTORY = 50


def _run_backtest(dirs: list) -> dict:
    """Pure CPU walk-forward loop (O(n^2)). Runs in a threadpool so it never
    blocks the async event loop / other clients while it crunches."""
    n = len(dirs)
    bets = wins = skipped_no_edge = skipped_no_direction = 0
    bins = [{"lo": lo, "hi": hi, "bets": 0, "wins": 0} for lo, hi in _CONF_BINS]

    for i in range(_BACKTEST_MIN_HISTORY, n):
        core = _markov_core(dirs[:i])
        if core is None or core["direction"] == "NONE":
            skipped_no_direction += 1
            continue
        p_up = core["prob_up"]
        if 0.45 <= p_up <= 0.55:          # live NO-EDGE gate
            skipped_no_edge += 1
            continue
        predicted = "U" if p_up >= 0.5 else "D"
        won = 1 if predicted == dirs[i] else 0
        bets += 1
        wins += won
        conf = p_up if predicted == "U" else 1 - p_up
        for b in bins:
            if b["lo"] <= conf < b["hi"]:
                b["bets"] += 1
                b["wins"] += won
                break

    win_rate = wins / bets if bets else 0.0
    lo, hi = _wilson(wins, bets)
    return {
        "candle_count": n,
        "bets": bets,
        "wins": wins,
        "win_rate": round(win_rate, 4),
        "ci95_low": round(lo, 4),
        "ci95_high": round(hi, 4),
        "edge_confirmed": lo > 0.5,       # lower bound above 50% = significant
        "skipped_no_edge": skipped_no_edge,
        "skipped_no_direction": skipped_no_direction,
        "by_confidence": [
            {
                "range": f"{b['lo']*100:g}-{b['hi']*100:g}%",
                "bets": b["bets"],
                "wins": b["wins"],
                "win_rate": round(b["wins"] / b["bets"], 4) if b["bets"] else None,
            }
            for b in bins
        ],
    }


@app.get("/api/backtest")
async def api_backtest(mode: str = Query("15M", pattern="^(5M|15M|1H)$")):
    """Walk-forward backtest: for each candle, predict it using ONLY the candles
    before it (same engine as /api/markov), then compare to what actually
    happened. Applies the live NO-EDGE gate (skip 0.45–0.55). Gives a large,
    honest, out-of-sample estimate of the strategy's edge in one shot.

    Caveat: idealised upper bound — ignores live timing/freshness and scores on
    our own candles, not Polymarket resolution.
    """
    candles, candle_source = await _get_candles(mode)
    if not candles or len(candles) <= _BACKTEST_MIN_HISTORY + 1:
        raise HTTPException(status_code=503, detail="Not enough candle data for a backtest")

    dirs = [_classify(c) for c in candles]
    # Offload the O(n^2) crunch to a thread so we don't freeze other requests.
    result = await asyncio.get_running_loop().run_in_executor(None, _run_backtest, dirs)
    result["mode"] = mode
    result["candle_source"] = candle_source
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


async def _fetch_poly_market(slug: str) -> Optional[dict]:
    """Fetch the raw Gamma market dict for a slug (or None if not found).
    Shared by the /api/poly_resolution endpoint and the background scheduler.
    Raises on network error so each caller decides how to handle it."""
    url = f"{GAMMA_API_BASE}/markets"
    # Markets resolved by default are hidden → must pass closed=true
    params = {"slug": slug, "closed": "true"}
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(url, params=params)
        data = r.json()
    markets = data if isinstance(data, list) else [data]
    return markets[0] if markets else None


@app.get("/api/poly_resolution")
async def api_poly_resolution(slug: str = Query(...)):
    """
    Proxy to Gamma API. Returns resolved winner for a given market slug.
    Slug format: btc-updown-5m-{ts} or btc-updown-15m-{ts}
    No 1H market exists on Polymarket → frontend handles that locally.
    """
    try:
        market = await _fetch_poly_market(slug)
    except Exception as exc:
        logger.error("Gamma API exception: %s", exc)
        raise HTTPException(status_code=503, detail="Gamma API unavailable")

    winner = _resolve_market(market)

    return {
        "resolved": winner is not None,
        "winner": winner,
        "slug": slug,
        "market_id": market.get("id") if isinstance(market, dict) else None,
    }


class TrackerSync(BaseModel):
    entries: list = []


@app.get("/api/tracker")
async def api_tracker_get():
    """Return the shared prediction history (all devices read the same store)."""
    async with _tracker_lock:
        return {"entries": _load_tracker()}


@app.post("/api/tracker")
async def api_tracker_post(
    payload: TrackerSync,
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
):
    """Merge the client's entries into the shared store and return the result."""
    _require_tracker_key(x_api_key)
    if len(payload.entries) > _MAX_SYNC_ENTRIES:
        raise HTTPException(status_code=413, detail="Too many entries in one sync")
    async with _tracker_lock:
        merged = _merge_entries(_load_tracker(), payload.entries)
        _save_tracker(merged)
        return {"entries": merged}


@app.delete("/api/tracker")
async def api_tracker_delete(
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
):
    """Clear the shared history (used by the 'Reset history' button)."""
    _require_tracker_key(x_api_key)
    async with _tracker_lock:
        _save_tracker([])
        return {"entries": []}


@app.get("/health")
async def health():
    return {"status": "ok", "ts": int(time.time())}


# ---------------------------------------------------------------------------
# Server-side autonomous tracker scheduler (Option B)
#
# Mirrors the frontend tracker — the App.tsx recording loop + the useTracker
# reconcile — but runs in the backend so the prediction history accumulates 24/7
# without any open client. The decision logic is factored into PURE functions
# (like _markov_core / _resolve_market) so it is unit-tested without network or
# wall-clock. Entries use the exact same shape the frontend writes, so the two
# merge by id (a client and the server can both record the same window safely).
# ---------------------------------------------------------------------------

_TRACKER_TFS = ("5M", "15M", "1H")
_SCHEDULER_INTERVAL = 12          # seconds — same cadence as the client reconcile
_POLY_GRACE_SECONDS = 30          # wait after close before scoring (resolution lag)
_POLY_TIMEOUT_SECONDS = 720       # 12 min: fall back to local scoring if unresolved
_LOCAL_EXPIRE_MINUTES = 30        # no price to score with this long → EXPIRED


def _poly_slug(tf: str, window_start: int) -> Optional[str]:
    """Deterministic Polymarket slug. 1H has no market → None (scored locally).
    Must match the frontend getPolySlug exactly."""
    if tf == "5M":
        return f"btc-updown-5m-{window_start}"
    if tf == "15M":
        return f"btc-updown-15m-{window_start}"
    return None


def _prediction_is_recordable(core: Optional[dict], tf: str, window_start: int) -> bool:
    """Pure gate mirroring the App.tsx guards: only record when the pattern is
    fresh (uses the candle immediately before this window), has a real direction,
    and is not NO-EDGE (0.45–0.55)."""
    if not core:
        return False
    if core.get("last_closed_time") != window_start - CYCLE_SECONDS[tf]:
        return False  # stale: previous candle not published yet → retry next tick
    if core.get("direction") in (None, "NONE"):
        return False
    prob_up = core.get("prob_up")
    if prob_up is None or 0.45 <= prob_up <= 0.55:
        return False  # NO EDGE → record nothing (honesty rule #4)
    return True


def _build_prediction_entry(
    tf: str, window_start: int, core: dict, price_at_open: float, now: int
) -> dict:
    """Build a PENDING tracker entry in the same shape the frontend writes, plus
    recordedBy='server' for traceability (harmless extra field on merge)."""
    return {
        "id": f"{tf}-{window_start}",
        "tf": tf,
        "windowStart": window_start,
        "direction": core["direction"],
        "probUp": core["prob_up"],
        "probDown": core["prob_down"],
        "sampleSize": core["sample_size"],
        "confidence": core["confidence"],
        "priceAtOpen": price_at_open,
        "recordedAt": now * 1000,
        "result": "PENDING",
        "recordedBy": "server",
    }


def _score_pending(
    entry: dict, now: int, poly_winner: Optional[str], close_price: Optional[float]
) -> Optional[dict]:
    """Pure reconciliation for one PENDING entry. Returns an updated entry, or
    None to leave it pending. Mirrors the useTracker reconcile branching:
      - Polymarket verdict is authoritative for 5M/15M (resultSource 'poly').
      - After a 12-min timeout (5M/15M) or after the grace period (1H), fall back
        to local scoring against the closed candle (resultSource 'local').
      - No price to score with after 30 min → EXPIRED (never left hanging).
    `poly_winner`: winning outcome from Gamma, or None. `close_price`: close of
    the window's candle, or None if not in history yet."""
    tf = entry.get("tf")
    ws = entry.get("windowStart")
    if tf not in CYCLE_SECONDS or not isinstance(ws, (int, float)) or isinstance(ws, bool):
        return None
    ws = int(ws)
    window_end = ws + CYCLE_SECONDS[tf]
    if now < window_end + _POLY_GRACE_SECONDS:
        return None

    direction = entry.get("direction")
    slug = _poly_slug(tf, ws)

    # 1) Polymarket authoritative verdict (5M/15M)
    if slug and poly_winner:
        winner = str(poly_winner).lower()
        predicted = str(direction).lower()
        is_win = (predicted == "up" and winner == "up") or (
            predicted == "down" and winner == "down"
        )
        return {**entry, "result": "WIN" if is_win else "LOSS", "resultSource": "poly"}

    price_open = entry.get("priceAtOpen")

    def _local(price_close: float) -> dict:
        local_win = (direction == "UP" and price_close >= price_open) or (
            direction == "DOWN" and price_close < price_open
        )
        return {
            **entry,
            "result": "WIN" if local_win else "LOSS",
            "resultSource": "local",
            "priceAtClose": price_close,
        }

    if slug:
        # 5M/15M: only fall back to local after the Polymarket timeout.
        if now > window_end + _POLY_TIMEOUT_SECONDS:
            if close_price is not None and price_open is not None:
                return _local(close_price)
            if (now - window_end) / 60 > _LOCAL_EXPIRE_MINUTES:
                return {**entry, "result": "EXPIRED"}
        return None

    # 1H: no Polymarket market → local scoring after the grace period.
    if close_price is not None and price_open is not None:
        return _local(close_price)
    if (now - window_end) / 60 > _LOCAL_EXPIRE_MINUTES:
        return {**entry, "result": "EXPIRED"}
    return None


async def _fetch_poly_winner(slug: str) -> Optional[str]:
    """Scheduler-side wrapper: winning outcome for a slug, or None on any error
    (the tick must never crash because Gamma hiccuped)."""
    try:
        market = await _fetch_poly_market(slug)
    except Exception as exc:
        logger.warning("Gamma API exception (scheduler) [%s]: %s", slug, exc)
        return None
    return _resolve_market(market)


async def _scheduler_tick() -> None:
    """One record + reconcile pass over all timeframes."""
    now = int(time.time())

    # Fetch candles once per TF (cached, ~10s TTL) and reuse for both phases.
    candles_by_tf: dict = {}
    for tf in _TRACKER_TFS:
        candles, _src = await _get_candles(tf)
        candles_by_tf[tf] = candles or []

    async with _tracker_lock:
        store = _load_tracker()
    existing_ids = {e.get("id") for e in store}

    changes: list = []

    # ---- RECORD: at most one prediction per TF per window ----
    for tf in _TRACKER_TFS:
        cycle = CYCLE_SECONDS[tf]
        ws = (now // cycle) * cycle
        if f"{tf}-{ws}" in existing_ids:
            continue  # anti-duplicate (rule #5)
        candles = candles_by_tf[tf]
        if not candles:
            continue
        closed = [c for c in candles if int(c["time"]) < ws]  # exclude forming candle
        core = _compute_markov(closed)
        if not _prediction_is_recordable(core, tf, ws):
            continue
        price_at_open = next(
            (float(c["open"]) for c in candles if int(c["time"]) == ws), None
        )
        if price_at_open is None:
            continue  # current candle not in history yet → retry next tick
        changes.append(_build_prediction_entry(tf, ws, core, price_at_open, now))

    # ---- RECONCILE: score every PENDING entry whose window has closed ----
    for entry in store:
        if entry.get("result") != "PENDING":
            continue
        tf = entry.get("tf")
        ws = entry.get("windowStart")
        if tf not in CYCLE_SECONDS or not isinstance(ws, (int, float)) or isinstance(ws, bool):
            continue
        ws = int(ws)
        if now < ws + CYCLE_SECONDS[tf] + _POLY_GRACE_SECONDS:
            continue  # not closed + grace yet — cheap pre-filter before any Gamma call
        slug = _poly_slug(tf, ws)
        poly_winner = await _fetch_poly_winner(slug) if slug else None
        candle = next((c for c in candles_by_tf.get(tf, []) if int(c["time"]) == ws), None)
        close_price = float(candle["close"]) if candle is not None else None
        updated = _score_pending(entry, now, poly_winner, close_price)
        if updated is not None:
            changes.append(updated)

    if changes:
        async with _tracker_lock:
            merged = _merge_entries(_load_tracker(), changes)
            _save_tracker(merged)
        recorded = sum(1 for c in changes if c.get("result") == "PENDING")
        logger.info("Scheduler: +%d recorded, %d scored", recorded, len(changes) - recorded)


async def _scheduler_loop() -> None:
    """Run _scheduler_tick forever; never dies on a transient error."""
    await asyncio.sleep(5)  # let the app finish starting / warm the candle cache
    while True:
        try:
            await _scheduler_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Scheduler tick failed: %s", exc)
        await asyncio.sleep(_SCHEDULER_INTERVAL)
