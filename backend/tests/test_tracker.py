"""
Tests for the shared tracker store: the merge rule (_merge_entries) and the
GET/POST/DELETE endpoints. Uses a temp store path — no real disk file touched.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ---------------------------------------------------------------------------
# _merge_entries — merge by id, scored result wins over PENDING
# ---------------------------------------------------------------------------

def test_merge_adds_new_ids():
    existing = [{"id": "15M-100", "result": "PENDING", "recordedAt": 1, "windowStart": 1_781_000_000}]
    incoming = [{"id": "15M-200", "result": "PENDING", "recordedAt": 2, "windowStart": 1_781_000_900}]
    merged = main._merge_entries(existing, incoming)
    assert [e["id"] for e in merged] == ["15M-100", "15M-200"]


def test_merge_scored_overrides_pending():
    existing = [{"id": "15M-100", "result": "PENDING", "recordedAt": 1, "windowStart": 1_781_000_000}]
    incoming = [{"id": "15M-100", "result": "WIN", "recordedAt": 1, "windowStart": 1_781_000_000}]
    merged = main._merge_entries(existing, incoming)
    assert len(merged) == 1
    assert merged[0]["result"] == "WIN"


def test_merge_pending_never_downgrades_scored():
    existing = [{"id": "15M-100", "result": "LOSS", "recordedAt": 1, "windowStart": 1_781_000_000}]
    incoming = [{"id": "15M-100", "result": "PENDING", "recordedAt": 1, "windowStart": 1_781_000_000}]
    merged = main._merge_entries(existing, incoming)
    assert merged[0]["result"] == "LOSS"


def test_merge_both_pending_takes_incoming_fields():
    existing = [{"id": "15M-100", "result": "PENDING", "recordedAt": 1, "windowStart": 1_781_000_000}]
    incoming = [{"id": "15M-100", "result": "PENDING", "recordedAt": 1, "windowStart": 1_781_000_000, "priceAtClose": 42.0}]
    merged = main._merge_entries(existing, incoming)
    assert merged[0]["priceAtClose"] == 42.0


def test_merge_drops_implausible_window_start():
    # windowStart before 2020 (or tiny test values) must be rejected outright.
    existing = [{"id": "15M-good", "result": "WIN", "windowStart": 1_781_000_000}]
    incoming = [
        {"id": "15M-1000", "result": "WIN", "windowStart": 1000},
        {"id": "15M-nows", "result": "WIN"},  # no windowStart
    ]
    merged = main._merge_entries(existing, incoming)
    assert [e["id"] for e in merged] == ["15M-good"]


# ---------------------------------------------------------------------------
# Endpoints — round-trip through a temp store file
# ---------------------------------------------------------------------------

def test_tracker_endpoints_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TRACKER_STORE_PATH", str(tmp_path / "store.json"))
    client = TestClient(main.app)

    assert client.get("/api/tracker").json() == {"entries": []}

    e1 = {"id": "15M-100", "result": "PENDING", "recordedAt": 1, "windowStart": 1_781_000_000}
    r = client.post("/api/tracker", json={"entries": [e1]})
    assert [e["id"] for e in r.json()["entries"]] == ["15M-100"]

    # A second device scores it → result upgrades, no duplicate.
    e1_won = {"id": "15M-100", "result": "WIN", "recordedAt": 1, "windowStart": 1_781_000_000}
    r = client.post("/api/tracker", json={"entries": [e1_won]})
    entries = r.json()["entries"]
    assert len(entries) == 1 and entries[0]["result"] == "WIN"

    # Persisted across a fresh GET.
    assert client.get("/api/tracker").json()["entries"][0]["result"] == "WIN"

    # Reset clears the shared store.
    assert client.delete("/api/tracker").json() == {"entries": []}
    assert client.get("/api/tracker").json() == {"entries": []}


# ---------------------------------------------------------------------------
# Write protection (X-Api-Key) and payload cap
# ---------------------------------------------------------------------------

def _pending(i):
    return {"id": f"15M-{i}", "result": "PENDING", "windowStart": 1_781_000_000 + i}


def test_write_requires_key_when_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TRACKER_STORE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(main, "TRACKER_API_KEY", "secret")
    client = TestClient(main.app)
    body = {"entries": [_pending(1)]}

    assert client.post("/api/tracker", json=body).status_code == 401          # no key
    assert client.delete("/api/tracker").status_code == 401
    assert client.post("/api/tracker", json=body,
                       headers={"X-Api-Key": "nope"}).status_code == 401       # wrong key
    assert client.post("/api/tracker", json=body,
                       headers={"X-Api-Key": "secret"}).status_code == 200     # right key
    assert client.delete("/api/tracker",
                         headers={"X-Api-Key": "secret"}).status_code == 200
    # GET stays open regardless.
    assert client.get("/api/tracker").status_code == 200


def test_write_open_when_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TRACKER_STORE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(main, "TRACKER_API_KEY", "")
    client = TestClient(main.app)
    assert client.post("/api/tracker", json={"entries": [_pending(1)]}).status_code == 200


def test_rejects_oversized_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TRACKER_STORE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(main, "TRACKER_API_KEY", "")
    monkeypatch.setattr(main, "_MAX_SYNC_ENTRIES", 3)
    client = TestClient(main.app)
    body = {"entries": [_pending(i) for i in range(5)]}
    assert client.post("/api/tracker", json=body).status_code == 413
