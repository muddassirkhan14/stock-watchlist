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
# New shape:
#   { "us":    { "lists": [ { id, label, stocks: [ {ticker, exchange, notes, order}, ... ] }, ... ] },
#     "india": { "lists": [ ... ] } }
# Old shape (auto-migrated): { "us": [...], "india": [...] }

SECTIONS = ("us", "india")
DEFAULT_LIST_ID    = "watchlist"
DEFAULT_LIST_LABEL = "Watchlist"


def _empty_section() -> dict:
    return {"lists": [{"id": DEFAULT_LIST_ID, "label": DEFAULT_LIST_LABEL, "stocks": []}]}


def _migrate_section(value) -> dict:
    """Accept either old (list) or new (dict with 'lists') shape and return the new shape."""
    if isinstance(value, list):
        return {"lists": [{"id": DEFAULT_LIST_ID, "label": DEFAULT_LIST_LABEL, "stocks": value}]}
    if isinstance(value, dict):
        lists = value.get("lists")
        if not isinstance(lists, list) or not lists:
            return _empty_section()
        clean = []
        for lst in lists:
            if not isinstance(lst, dict):
                continue
            clean.append({
                "id":     str(lst.get("id") or DEFAULT_LIST_ID),
                "label":  str(lst.get("label") or DEFAULT_LIST_LABEL),
                "stocks": list(lst.get("stocks") or []),
            })
        return {"lists": clean or _empty_section()["lists"]}
    return _empty_section()


def load_watchlist() -> dict:
    if WATCHLIST_FILE.exists():
        raw = json.loads(WATCHLIST_FILE.read_text())
    else:
        raw = {}
    return {sec: _migrate_section(raw.get(sec)) for sec in SECTIONS}


def save_watchlist(wl: dict) -> None:
    out = {sec: _migrate_section(wl.get(sec)) for sec in SECTIONS}
    WATCHLIST_FILE.write_text(json.dumps(out, indent=2))


# ── List helpers ──────────────────────────────────────────────

def find_list(wl: dict, section: str, list_id: str):
    """Return the sub-list dict, or None."""
    for lst in wl.get(section, {}).get("lists", []):
        if lst.get("id") == list_id:
            return lst
    return None


def _slug(label: str) -> str:
    s = "".join(c.lower() if c.isalnum() else "_" for c in (label or "").strip())
    s = "_".join(filter(None, s.split("_")))
    return s or "list"


def add_list(wl: dict, section: str, label: str) -> str:
    """Create a new sub-list. Returns the assigned id."""
    section_obj = wl.setdefault(section, _empty_section())
    section_obj.setdefault("lists", [])
    existing_ids = {lst.get("id") for lst in section_obj["lists"]}
    base = _slug(label)
    new_id = base
    n = 2
    while new_id in existing_ids:
        new_id = f"{base}_{n}"
        n += 1
    section_obj["lists"].append({"id": new_id, "label": label.strip() or DEFAULT_LIST_LABEL, "stocks": []})
    return new_id


def rename_list(wl: dict, section: str, list_id: str, new_label: str) -> bool:
    lst = find_list(wl, section, list_id)
    if not lst:
        return False
    lst["label"] = (new_label or "").strip() or lst["label"]
    return True


def delete_list(wl: dict, section: str, list_id: str) -> bool:
    """Remove a list. Refuses to delete the last list in a section."""
    section_obj = wl.get(section) or {}
    lists = section_obj.get("lists") or []
    if len(lists) <= 1:
        return False
    section_obj["lists"] = [lst for lst in lists if lst.get("id") != list_id]
    return len(section_obj["lists"]) < len(lists)


def reorder_lists(wl: dict, section: str, new_order: list) -> bool:
    section_obj = wl.get(section) or {}
    by_id = {lst.get("id"): lst for lst in section_obj.get("lists", [])}
    ordered = [by_id[i] for i in new_order if i in by_id]
    # Append any lists the client forgot to mention so we never lose data.
    for lid, lst in by_id.items():
        if lst not in ordered:
            ordered.append(lst)
    if not ordered:
        return False
    section_obj["lists"] = ordered
    return True


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
