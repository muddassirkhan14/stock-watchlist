"""
app.py — Flask routes only. Business logic lives in core/.
"""

import re
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from core.models  import merge
from core.storage import (
    load_watchlist, save_watchlist,
    load_cache, save_cache,
    load_prefs, save_prefs,
    SECTIONS, DEFAULT_LIST_ID,
    find_list, add_list, rename_list, delete_list, reorder_lists,
)
from core.yahoo   import (
    fetch_quote, refresh_all_prices, refresh_all_prices_async,
    get_session_and_crumb,
    get_history_cached, compute_return,
    search_tickers,
    REFRESH_STATUS,
)

app = Flask(__name__, static_folder="frontend", static_url_path="")
CORS(app)


# ── Helpers ───────────────────────────────────────────────────

def _active_list_for(section: str, prefs: dict, wl: dict) -> str:
    """Return the persisted active list id for `section`, or the first available list."""
    persisted = (prefs.get(section) or {}).get("activeList")
    lists = wl.get(section, {}).get("lists", [])
    ids   = [lst["id"] for lst in lists]
    if persisted in ids:
        return persisted
    return ids[0] if ids else DEFAULT_LIST_ID


def get_full_data() -> dict:
    """Build the response shape: {section: {active, lists:[{id,label,stocks:[merged]}]}}."""
    wl    = load_watchlist()
    cache = load_cache()
    prefs = load_prefs()
    out   = {}
    for section in SECTIONS:
        section_obj = wl.get(section, {"lists": []})
        merged_lists = [
            {
                "id":     lst["id"],
                "label":  lst["label"],
                "stocks": [merge(e, cache) for e in lst.get("stocks", [])],
            }
            for lst in section_obj.get("lists", [])
        ]
        out[section] = {
            "active": _active_list_for(section, prefs, wl),
            "lists":  merged_lists,
        }
    return out


def _get_or_400(body: dict, key: str):
    v = body.get(key)
    if not v or not isinstance(v, str):
        return None
    return v


# ── Routes ────────────────────────────────────────────────────

@app.route("/api/stocks")
def get_stocks():
    return jsonify(get_full_data())


@app.route("/api/add", methods=["POST"])
def add_stock():
    body     = request.get_json() or {}
    raw      = body.get("input", "").strip()
    section  = body.get("section", "us")
    list_id  = body.get("list",    DEFAULT_LIST_ID)

    if section not in SECTIONS:
        return jsonify({"ok": False, "error": "Invalid section"}), 400

    match = re.match(r"^([A-Z0-9.&\-]+)\(([A-Z]+)\)$", raw.upper())
    if not match:
        return jsonify({"ok": False, "error": "Format: TICKER(EXCHANGE) — e.g. AAPL(NASDAQ)"}), 400

    ticker   = match.group(1).replace("-", ".")
    exchange = match.group(2)
    wl       = load_watchlist()
    target   = find_list(wl, section, list_id)
    if not target:
        return jsonify({"ok": False, "error": f"List '{list_id}' not found in {section}"}), 404

    if any(e["ticker"].upper() == ticker for e in target["stocks"]):
        return jsonify({"ok": False, "error": f"{ticker} already in this list"}), 400

    print(f"\nFetching: {ticker} ({exchange})")
    session, crumb = get_session_and_crumb()
    if not session:
        return jsonify({"ok": False, "error": "Could not connect to Yahoo Finance"}), 500

    result = fetch_quote(ticker, exchange, session, crumb)
    if not result["ok"]:
        return jsonify({"ok": False, "error": result["error"]}), 400

    wl_entry = {
        "ticker":   ticker,
        "exchange": exchange,
        "notes":    "",
        "order":    len(target["stocks"]),
    }
    target["stocks"].append(wl_entry)
    save_watchlist(wl)

    cache = load_cache()
    cache[ticker] = {k: v for k, v in result.items() if k != "ok"}
    save_cache(cache)

    stock = merge(wl_entry, cache)
    print(f"  Saved: {ticker} @ ${stock['price']}  P/E={stock['pe']}")
    return jsonify({"ok": True, "stock": stock})


@app.route("/api/delete", methods=["DELETE"])
def delete_stock():
    body    = request.get_json() or {}
    ticker  = body.get("ticker", "").upper()
    section = body.get("section", "us")
    list_id = body.get("list",    DEFAULT_LIST_ID)
    wl      = load_watchlist()
    target  = find_list(wl, section, list_id)
    if not target:
        return jsonify({"ok": False, "error": "List not found"}), 404

    before  = len(target["stocks"])
    target["stocks"] = [e for e in target["stocks"] if e["ticker"].upper() != ticker]
    if len(target["stocks"]) == before:
        return jsonify({"ok": False, "error": "Ticker not found"}), 404

    save_watchlist(wl)

    # Only purge the cache entry if the ticker is gone from EVERY list across sections.
    still_used = any(
        e["ticker"].upper() == ticker
        for sec in wl.values()
        for lst in sec.get("lists", [])
        for e in lst.get("stocks", [])
    )
    if not still_used:
        cache = load_cache()
        if cache.pop(ticker, None) is not None:
            save_cache(cache)
    return jsonify({"ok": True})


@app.route("/api/note", methods=["PATCH"])
def update_note():
    body    = request.get_json() or {}
    ticker  = body.get("ticker", "").upper()
    note    = body.get("note", "")
    section = body.get("section", "us")
    list_id = body.get("list",    DEFAULT_LIST_ID)
    wl      = load_watchlist()
    target  = find_list(wl, section, list_id)
    if not target:
        return jsonify({"ok": False, "error": "List not found"}), 404

    for entry in target["stocks"]:
        if entry["ticker"].upper() == ticker:
            entry["notes"] = note
            save_watchlist(wl)
            return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "Ticker not found"}), 404


@app.route("/api/reorder", methods=["POST"])
def reorder():
    body      = request.get_json() or {}
    section   = body.get("section", "us")
    list_id   = body.get("list",    DEFAULT_LIST_ID)
    new_order = body.get("order",   [])
    wl        = load_watchlist()
    target    = find_list(wl, section, list_id)
    if not target:
        return jsonify({"ok": False, "error": "List not found"}), 404

    entry_map  = {e["ticker"].upper(): e for e in target["stocks"]}
    target["stocks"] = [entry_map[t.upper()] for t in new_order if t.upper() in entry_map]
    save_watchlist(wl)
    return jsonify({"ok": True})


@app.route("/api/refresh", methods=["POST"])
def refresh():
    """Kick off a background price refresh. Non-blocking."""
    started = refresh_all_prices_async()
    return jsonify({
        "ok":      True,
        "started": started,
        "status":  REFRESH_STATUS,
    })


@app.route("/api/status")
def status():
    """Current background-refresh progress."""
    return jsonify(REFRESH_STATUS)


@app.route("/api/search")
def search():
    """Free-text Yahoo Finance search. Query: ?q=<text>."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": True, "results": []})
    return jsonify({"ok": True, "results": search_tickers(q)})


# ── Sub-list management ───────────────────────────────────────

@app.route("/api/lists", methods=["POST"])
def create_list():
    body    = request.get_json() or {}
    section = body.get("section")
    label   = (body.get("label") or "").strip()
    if section not in SECTIONS:
        return jsonify({"ok": False, "error": "Invalid section"}), 400
    if not label:
        return jsonify({"ok": False, "error": "Label is required"}), 400

    wl     = load_watchlist()
    new_id = add_list(wl, section, label)
    save_watchlist(wl)
    return jsonify({"ok": True, "id": new_id, "label": label})


@app.route("/api/lists/<section>/<list_id>", methods=["PATCH"])
def patch_list(section, list_id):
    if section not in SECTIONS:
        return jsonify({"ok": False, "error": "Invalid section"}), 400
    body  = request.get_json() or {}
    label = (body.get("label") or "").strip()
    if not label:
        return jsonify({"ok": False, "error": "Label is required"}), 400
    wl = load_watchlist()
    if not rename_list(wl, section, list_id, label):
        return jsonify({"ok": False, "error": "List not found"}), 404
    save_watchlist(wl)
    return jsonify({"ok": True})


@app.route("/api/lists/<section>/<list_id>", methods=["DELETE"])
def remove_list(section, list_id):
    if section not in SECTIONS:
        return jsonify({"ok": False, "error": "Invalid section"}), 400
    wl = load_watchlist()
    if not delete_list(wl, section, list_id):
        return jsonify({"ok": False, "error": "Cannot delete the last list in a section"}), 400
    save_watchlist(wl)

    # If the deleted list was the active one, fall back to the first remaining list.
    prefs = load_prefs()
    sec_prefs = prefs.get(section) or {}
    if sec_prefs.get("activeList") == list_id:
        remaining = wl[section]["lists"]
        sec_prefs["activeList"] = remaining[0]["id"] if remaining else DEFAULT_LIST_ID
        prefs[section] = sec_prefs
        save_prefs(prefs)
    return jsonify({"ok": True})


@app.route("/api/lists/<section>/order", methods=["PUT"])
def reorder_lists_route(section):
    if section not in SECTIONS:
        return jsonify({"ok": False, "error": "Invalid section"}), 400
    body  = request.get_json() or {}
    order = body.get("order")
    if not isinstance(order, list):
        return jsonify({"ok": False, "error": "order must be a list of list ids"}), 400
    wl = load_watchlist()
    if not reorder_lists(wl, section, [str(i) for i in order]):
        return jsonify({"ok": False, "error": "Section has no lists"}), 400
    save_watchlist(wl)
    return jsonify({"ok": True})


# ── UI preferences (column order + visibility + active sub-tab) ────────────────

@app.route("/api/prefs", methods=["GET"])
def get_prefs():
    return jsonify(load_prefs())


@app.route("/api/prefs", methods=["PUT"])
def put_prefs():
    """
    Merge-update preferences. Body: { section, order?, visible?, activeList? }
    where visible is a partial map { colId: bool }.
    """
    body    = request.get_json() or {}
    section = body.get("section")
    if section not in SECTIONS:
        return jsonify({"ok": False, "error": "Invalid section"}), 400

    prefs   = load_prefs()
    current = prefs.get(section) or {}
    order   = current.get("order")
    visible = current.get("visible") or {}
    active  = current.get("activeList")

    if isinstance(body.get("order"), list):
        order = [str(x) for x in body["order"]]
    if isinstance(body.get("visible"), dict):
        for k, v in body["visible"].items():
            visible[str(k)] = bool(v)
    if isinstance(body.get("activeList"), str):
        active = body["activeList"]

    new_section_prefs = {"order": order or [], "visible": visible}
    if active:
        new_section_prefs["activeList"] = active
    prefs[section] = new_section_prefs
    save_prefs(prefs)
    return jsonify({"ok": True, "prefs": prefs[section]})


# ── Date-range performance ────────────────────────────────────

def _parse_iso_date(s: str):
    """Parse 'YYYY-MM-DD' → unix timestamp (start of day, local tz). None on failure."""
    if not s:
        return None
    try:
        return int(datetime.strptime(s, "%Y-%m-%d").timestamp())
    except Exception:
        return None


@app.route("/api/returns", methods=["POST"])
def returns():
    """
    Compute return % for every ticker in a (section, list) between `from` and `to`.
    Body: { section, list, from: 'YYYY-MM-DD', to: 'YYYY-MM-DD' }
    Returns: { "TICKER": { start, end, return_pct, return_abs, high, low } | null, ... }
    """
    body    = request.get_json() or {}
    section = body.get("section", "us")
    list_id = body.get("list",    DEFAULT_LIST_ID)
    from_ts = _parse_iso_date(body.get("from"))
    to_ts   = _parse_iso_date(body.get("to"))
    if from_ts is None or to_ts is None or from_ts >= to_ts:
        return jsonify({"ok": False, "error": "Invalid date range"}), 400

    wl     = load_watchlist()
    target = find_list(wl, section, list_id)
    if not target:
        return jsonify({"ok": False, "error": "List not found"}), 404

    session, crumb = get_session_and_crumb()
    out = {}
    for entry in target["stocks"]:
        ticker   = entry["ticker"]
        exchange = entry["exchange"]
        series   = get_history_cached(ticker, exchange, session, crumb)
        out[ticker] = compute_return(series, from_ts, to_ts)

    return jsonify({"ok": True, "returns": out})


@app.route("/api/history/<ticker>")
def history(ticker):
    """
    Return sliced daily close series for one ticker between `from` and `to`.
    Query: ?from=YYYY-MM-DD&to=YYYY-MM-DD&section=us|india
    The ticker is looked up across all sub-lists in the given section.
    """
    ticker  = ticker.upper()
    section = request.args.get("section", "us")
    from_ts = _parse_iso_date(request.args.get("from"))
    to_ts   = _parse_iso_date(request.args.get("to"))
    if from_ts is None or to_ts is None or from_ts >= to_ts:
        return jsonify({"ok": False, "error": "Invalid date range"}), 400

    wl    = load_watchlist()
    entry = None
    for lst in wl.get(section, {}).get("lists", []):
        for e in lst.get("stocks", []):
            if e["ticker"].upper() == ticker:
                entry = e
                break
        if entry:
            break
    if not entry:
        return jsonify({"ok": False, "error": "Ticker not in watchlist"}), 404

    series = get_history_cached(entry["ticker"], entry["exchange"])
    sliced = [[ts, cl] for ts, cl in series if from_ts <= ts <= to_ts]
    stats  = compute_return(series, from_ts, to_ts)

    return jsonify({
        "ok":     True,
        "ticker": ticker,
        "series": sliced,
        "stats":  stats,
    })


# ── Serve frontend ────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    return send_from_directory("frontend", "index.html")


# ── Startup ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Stock Watchlist ===")
    print("Starting server — prices will refresh in the background.")
    refresh_all_prices_async()
    print("\nOpen http://localhost:7080\n")
    app.run(port=7080, debug=False)
