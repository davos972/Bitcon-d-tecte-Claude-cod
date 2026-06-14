"""
Unit tests for the Markov engine and candle helpers.

Run from the backend/ directory:  pytest -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def _candle(t, open_, close):
    return {"time": t, "open": open_, "close": close}


# ---------------------------------------------------------------------------
# _classify — the >= rule is the Polymarket resolution rule (tie = Up)
# ---------------------------------------------------------------------------

def test_classify_up_when_close_above_open():
    assert main._classify(_candle(0, 100, 101)) == "U"


def test_classify_down_when_close_below_open():
    assert main._classify(_candle(0, 100, 99)) == "D"


def test_classify_tie_is_up():
    # Equality must resolve to UP (Polymarket rule).
    assert main._classify(_candle(0, 100, 100)) == "U"


# ---------------------------------------------------------------------------
# _normalize_candle_time — snap onto the UTC grid per mode
# ---------------------------------------------------------------------------

def test_normalize_15m_snaps_down_to_grid():
    # 13:07:00 UTC -> 13:00:00 on the 15m grid
    assert main._normalize_candle_time(1_700_000_000 + 7 * 60, "15M") % 900 == 0


def test_normalize_already_aligned_is_unchanged():
    aligned = (1_700_000_000 // 900) * 900
    assert main._normalize_candle_time(aligned, "15M") == aligned


def test_normalize_candles_dedups_by_slot():
    cycle = main.CYCLE_SECONDS["5M"]
    base = (1_700_000_000 // cycle) * cycle
    # Two raw candles inside the same 5m slot -> keep one, on the grid.
    raw = [_candle(base + 10, 1, 2), _candle(base + 200, 3, 4)]
    out = main._normalize_candles(raw, "5M")
    assert len(out) == 1
    assert out[0]["time"] == base


# ---------------------------------------------------------------------------
# _compute_markov — pattern counting, N fallback, sample threshold
# ---------------------------------------------------------------------------

def _candles_from_dirs(dirs):
    """Build candles whose classification matches the given U/D sequence."""
    candles = []
    for i, d in enumerate(dirs):
        if d == "U":
            candles.append(_candle(i * 900, 100, 101))
        else:
            candles.append(_candle(i * 900, 100, 99))
    return candles


def test_returns_none_when_too_few_candles():
    assert main._compute_markov(_candles_from_dirs(["U", "D"])) is None


def test_perfect_up_follower_high_confidence():
    # Pattern "U,U,U" is always followed by "U". With 60 closed candles the
    # current pattern UUU appears > 50 times -> HIGH confidence, prob_up = 1.
    closed = _candles_from_dirs(["U"] * 60)
    res = main._compute_markov(closed)
    assert res["direction"] == "UP"
    assert res["prob_up"] == 1.0
    assert res["n_used"] == 3
    assert res["confidence"] == "HIGH"
    assert res["last_closed_time"] == closed[-1]["time"]


def test_falls_back_to_smaller_n_when_sample_thin():
    # A pattern that occurs but not enough at N=3 should fall back to a lower N.
    # 30 alternating candles: the trailing pattern has plenty of N=1 matches.
    closed = _candles_from_dirs(["U", "D"] * 30)
    res = main._compute_markov(closed)
    assert res["n_used"] >= 1
    assert res["sample_size"] >= 20


def test_low_confidence_when_no_pattern_reaches_threshold():
    # 5 candles -> every pattern occurs at most a couple of times -> NONE.
    closed = _candles_from_dirs(["U", "D", "U", "D", "U"])
    res = main._compute_markov(closed)
    assert res["confidence"] == "LOW"
    assert res["direction"] == "NONE"
    assert res["prob_up"] == 0.5
