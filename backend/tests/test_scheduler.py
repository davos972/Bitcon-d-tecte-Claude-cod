"""
Tests for the server-side autonomous tracker scheduler (Option B).

Only the PURE decision functions are exercised — no network, no wall-clock, no
disk. That mirrors how the engine (_markov_core) and Polymarket parsing
(_resolve_market) are tested elsewhere.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

# A 15M window start aligned to both the 300s and 900s grids.
WS15 = 1_781_001_000          # divisible by 900
CYCLE15 = main.CYCLE_SECONDS["15M"]


def _core(prob_up, direction, last_closed):
    """Minimal _compute_markov-shaped dict for the recordable gate."""
    return {
        "prob_up": prob_up,
        "prob_down": round(1 - prob_up, 4),
        "direction": direction,
        "sample_size": 120,
        "confidence": "HIGH",
        "last_closed_time": last_closed,
    }


# ---------------------------------------------------------------------------
# _poly_slug — must match the frontend getPolySlug
# ---------------------------------------------------------------------------

def test_poly_slug_5m_15m_and_none_for_1h():
    assert main._poly_slug("5M", 300) == "btc-updown-5m-300"
    assert main._poly_slug("15M", 900) == "btc-updown-15m-900"
    assert main._poly_slug("1H", 3600) is None


# ---------------------------------------------------------------------------
# _prediction_is_recordable — freshness + direction + NO-EDGE gate
# ---------------------------------------------------------------------------

def test_recordable_true_when_fresh_and_has_edge():
    core = _core(0.56, "UP", WS15 - CYCLE15)
    assert main._prediction_is_recordable(core, "15M", WS15) is True


def test_not_recordable_when_stale():
    # last_closed_time is the PREVIOUS window's — pattern not refreshed yet.
    core = _core(0.56, "UP", WS15 - 2 * CYCLE15)
    assert main._prediction_is_recordable(core, "15M", WS15) is False


def test_not_recordable_on_no_edge():
    core = _core(0.52, "UP", WS15 - CYCLE15)          # inside 0.45–0.55
    assert main._prediction_is_recordable(core, "15M", WS15) is False


def test_not_recordable_when_direction_none():
    core = _core(0.50, "NONE", WS15 - CYCLE15)
    assert main._prediction_is_recordable(core, "15M", WS15) is False


def test_not_recordable_when_core_none():
    assert main._prediction_is_recordable(None, "15M", WS15) is False


def test_edge_boundaries_just_outside_gate_are_recordable():
    assert main._prediction_is_recordable(_core(0.5501, "UP", WS15 - CYCLE15), "15M", WS15) is True
    assert main._prediction_is_recordable(_core(0.4499, "DOWN", WS15 - CYCLE15), "15M", WS15) is True


# ---------------------------------------------------------------------------
# _build_prediction_entry — shape matches the frontend entry
# ---------------------------------------------------------------------------

def test_build_entry_shape():
    core = _core(0.56, "UP", WS15 - CYCLE15)
    e = main._build_prediction_entry("15M", WS15, core, 65000.0, 1_781_001_005)
    assert e["id"] == f"15M-{WS15}"
    assert e["result"] == "PENDING"
    assert e["direction"] == "UP"
    assert e["probUp"] == 0.56
    assert e["priceAtOpen"] == 65000.0
    assert e["recordedAt"] == 1_781_001_005 * 1000
    assert e["recordedBy"] == "server"
    # Must survive the store plausibility guard.
    assert main._is_plausible(e)


# ---------------------------------------------------------------------------
# _score_pending — the reconcile branching
# ---------------------------------------------------------------------------

def _pending(tf, ws, direction, price_open=100.0):
    return {
        "id": f"{tf}-{ws}",
        "tf": tf,
        "windowStart": ws,
        "direction": direction,
        "priceAtOpen": price_open,
        "result": "PENDING",
    }


def test_stays_pending_before_grace():
    ws = WS15
    e = _pending("15M", ws, "UP")
    now = ws + CYCLE15 + 10          # closed but within the 30s grace
    assert main._score_pending(e, now, None, None) is None


def test_poly_win_when_winner_matches_direction():
    ws = WS15
    e = _pending("15M", ws, "UP")
    now = ws + CYCLE15 + 60
    out = main._score_pending(e, now, "Up", None)
    assert out["result"] == "WIN"
    assert out["resultSource"] == "poly"


def test_poly_loss_when_winner_opposite():
    ws = WS15
    e = _pending("15M", ws, "DOWN")
    now = ws + CYCLE15 + 60
    out = main._score_pending(e, now, "Up", None)
    assert out["result"] == "LOSS"
    assert out["resultSource"] == "poly"


def test_5m15m_stays_pending_before_timeout_when_unresolved():
    ws = WS15
    e = _pending("15M", ws, "UP")
    now = ws + CYCLE15 + 120          # past grace, before 12-min timeout
    assert main._score_pending(e, now, None, 105.0) is None


def test_5m15m_local_fallback_after_timeout():
    ws = WS15
    e = _pending("15M", ws, "UP", price_open=100.0)
    now = ws + CYCLE15 + main._POLY_TIMEOUT_SECONDS + 10
    out = main._score_pending(e, now, None, 105.0)   # close >= open, predicted UP → WIN
    assert out["result"] == "WIN" and out["resultSource"] == "local"
    assert out["priceAtClose"] == 105.0


def test_5m15m_expired_after_timeout_without_price():
    ws = WS15
    e = _pending("15M", ws, "UP")
    now = ws + CYCLE15 + main._POLY_TIMEOUT_SECONDS + main._LOCAL_EXPIRE_MINUTES * 60 + 10
    out = main._score_pending(e, now, None, None)
    assert out["result"] == "EXPIRED"


def test_1h_local_scoring_after_grace():
    cyc = main.CYCLE_SECONDS["1H"]
    ws = (1_781_000_000 // cyc) * cyc
    e = _pending("1H", ws, "DOWN", price_open=100.0)
    now = ws + cyc + 60
    out = main._score_pending(e, now, None, 99.0)     # close < open, predicted DOWN → WIN
    assert out["result"] == "WIN" and out["resultSource"] == "local"


def test_1h_tie_scores_as_up_loss_for_down_prediction():
    # Polymarket rule: close == open is UP. A DOWN prediction must LOSE on a tie.
    cyc = main.CYCLE_SECONDS["1H"]
    ws = (1_781_000_000 // cyc) * cyc
    e = _pending("1H", ws, "DOWN", price_open=100.0)
    now = ws + cyc + 60
    out = main._score_pending(e, now, None, 100.0)    # equal → UP → DOWN loses
    assert out["result"] == "LOSS"


def test_1h_expired_without_price_after_expiry():
    cyc = main.CYCLE_SECONDS["1H"]
    ws = (1_781_000_000 // cyc) * cyc
    e = _pending("1H", ws, "UP")
    now = ws + cyc + main._LOCAL_EXPIRE_MINUTES * 60 + 30
    out = main._score_pending(e, now, None, None)
    assert out["result"] == "EXPIRED"
