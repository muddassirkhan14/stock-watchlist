"""
storage.py — read/write watchlist.json (permanent) and cache.json (runtime prices)
"""

import json
from pathlib import Path

DATA_DIR       = Path("data")
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
CACHE_FILE     = DATA_DIR / "cache.json"
HISTORY_FILE   = DATA_DIR / "history.json"
PREFS_FILE     = DATA_DIR / "prefs.json"

DATA_DIR.mkdir(exist_ok=True)


# ── Watchlist (permanent) ─────────────────────────────────────
# { "us": [...], "india": [...] }
# Each entry: { ticker, exchange, notes, order }

def load_watchlist() -> dict:
    if WATCHLIST_FILE.exists():
        return json.loads(WATCHLIST_FILE.read_text())
    return {"us": [], "india": []}


def save_watchlist(wl: dict) -> None:
    wl.setdefault("us", [])
    wl.setdefault("india", [])
    WATCHLIST_FILE.write_text(json.dumps(wl, indent=2))


# ── Cache (runtime prices) ────────────────────────────────────
# { "TICKER": { name, price, change, change_pct, high, low, pe, sector, industry } }

def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache: dict) -> None:
    """Write cache to disk, sanitising any non-finite floats first."""
    CACHE_FILE.write_text(json.dumps(_sanitize(cache), indent=2))


def _sanitize(obj):
    """Recursively replace NaN / Infinity with 0.0 so JSON stays valid."""
    if isinstance(obj, float):
        return 0.0 if (obj != obj or obj == float("inf") or obj == float("-inf")) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    return obj


# ── Prefs (UI preferences: column order + visibility) ─────────
# { "us": { "order": [...colIds], "visible": { colId: bool } },
#   "india": { ... } }

def load_prefs() -> dict:
    if PREFS_FILE.exists():
        try:
            return json.loads(PREFS_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_prefs(prefs: dict) -> None:
    PREFS_FILE.write_text(json.dumps(prefs, indent=2))


# ── History (daily closes, cached with TTL) ───────────────────
# { "TICKER": { "last_fetched": "<iso>", "series": [[ts, close], ...] } }

def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_history(hist: dict) -> None:
    HISTORY_FILE.write_text(json.dumps(_sanitize(hist), indent=2))
