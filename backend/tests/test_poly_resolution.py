"""
Tests for Polymarket resolution parsing (_resolve_market) and the CryptoCompare
error detection. Uses frozen Gamma-API-shaped payloads — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


# ---------------------------------------------------------------------------
# _resolve_market — defensive parsing of the Gamma payload
# ---------------------------------------------------------------------------

def test_resolved_up_with_array_fields():
    market = {"outcomes": ["Up", "Down"], "outcomePrices": ["1.0", "0.0"]}
    assert main._resolve_market(market) == "Up"


def test_resolved_down_with_array_fields():
    market = {"outcomes": ["Up", "Down"], "outcomePrices": ["0.0", "1.0"]}
    assert main._resolve_market(market) == "Down"


def test_outcomes_and_prices_as_json_strings():
    # Gamma sometimes returns these fields as JSON-encoded strings.
    market = {
        "outcomes": '["Up", "Down"]',
        "outcomePrices": '["0.02", "0.98"]',
    }
    assert main._resolve_market(market) == "Down"


def test_pending_when_no_outcome_above_threshold():
    # Intermediate prices -> market still live -> not resolved.
    market = {"outcomes": ["Up", "Down"], "outcomePrices": ["0.55", "0.45"]}
    assert main._resolve_market(market) is None


def test_threshold_is_inclusive_at_0_9():
    market = {"outcomes": ["Up", "Down"], "outcomePrices": ["0.9", "0.1"]}
    assert main._resolve_market(market) == "Up"


def test_empty_or_missing_market():
    assert main._resolve_market(None) is None
    assert main._resolve_market({}) is None
    assert main._resolve_market({"outcomes": [], "outcomePrices": []}) is None


def test_garbage_prices_are_skipped():
    market = {"outcomes": ["Up", "Down"], "outcomePrices": ["n/a", "0.95"]}
    assert main._resolve_market(market) == "Down"


# ---------------------------------------------------------------------------
# _cc_error_message — must catch both legacy and current error shapes
# ---------------------------------------------------------------------------

def test_cc_legacy_error_shape():
    data = {"Response": "Error", "Message": "rate limit"}
    assert main._cc_error_message(data) == "rate limit"


def test_cc_new_err_shape():
    data = {"Data": {}, "Err": {"type": 2, "message": "API key required"}}
    assert main._cc_error_message(data) == "API key required"


def test_cc_no_error_returns_none():
    data = {"Response": "Success", "Data": {"Data": [{"time": 1}]}}
    assert main._cc_error_message(data) is None
