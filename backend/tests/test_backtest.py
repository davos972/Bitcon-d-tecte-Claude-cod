"""
Tests for the walk-forward backtest engine (_markov_core + /api/backtest).
Uses deterministic synthetic candles — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _up_candles(count):
    # close > open -> classified "U"
    return [{"time": i * 900, "open": 100, "close": 101} for i in range(count)]


# ---------------------------------------------------------------------------
# _markov_core — the shared engine core
# ---------------------------------------------------------------------------

def test_core_none_when_too_short():
    assert main._markov_core(["U", "D"]) is None


def test_core_perfect_up():
    core = main._markov_core(["U"] * 60)
    assert core["direction"] == "UP"
    assert core["prob_up"] == 1.0
    assert core["n_used"] == 3


def test_compute_markov_still_matches_core():
    # _compute_markov must keep delegating to the core and rounding as before.
    closed = _up_candles(60)
    res = main._compute_markov(closed)
    assert res["prob_up"] == 1.0 and res["prob_down"] == 0.0
    assert res["candle_count"] == 60
    assert res["last_closed_time"] == closed[-1]["time"]


# ---------------------------------------------------------------------------
# /api/backtest endpoint
# ---------------------------------------------------------------------------

def test_backtest_all_up(monkeypatch):
    candles = _up_candles(120)

    async def fake_candles(mode):
        return candles, "test"

    monkeypatch.setattr(main, "_get_candles", fake_candles)
    r = TestClient(main.app).get("/api/backtest?mode=15M").json()
    assert r["bets"] == 120 - main._BACKTEST_MIN_HISTORY
    assert r["wins"] == r["bets"]           # UUU -> always UP, always right here
    assert r["win_rate"] == 1.0
    assert r["edge_confirmed"] is True
    assert r["skipped_no_edge"] == 0
    assert r["ci95_low"] > 0.9


def test_backtest_too_few_candles(monkeypatch):
    async def fake_candles(mode):
        return _up_candles(10), "test"

    monkeypatch.setattr(main, "_get_candles", fake_candles)
    assert TestClient(main.app).get("/api/backtest?mode=5M").status_code == 503
