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
)
from core.yahoo   import (
    fetch_quote, refresh_all_prices, refresh_all_prices_async,
    get_session_and_crumb,
    get_history_cached, compute_return,
    REFRESH_STATUS,
)

app = Flask(__name__, static_folder="frontend", static_url_path="")
CORS(app)


# ── Helper ────────────────────────────────────────────────────

def get_full_data() -> dict:
    """Merge watchlist + cache into the full stock list served to the UI."""
    wl    = load_watchlist()
    cache = load_cache()
    return {
        "us":    [merge(e, cache) for e in wl.get("us",    [])],
        "india": [merge(e, cache) for e in wl.get("india", [])],
    }


# ── Routes ────────────────────────────────────────────────────

@app.route("/api/stocks")
def get_stocks():
    return jsonify(get_full_data())


@app.route("/api/add", methods=["POST"])
def add_stock():
    body    = request.get_json()
    raw     = body.get("input", "").strip()
    section = body.get("section", "us")

    match = re.match(r"^([A-Z0-9.\-]+)\(([A-Z]+)\)$", raw.upper())
    if not match:
        return jsonify({"ok": False, "error": "Format: TICKER(EXCHANGE) — e.g. AAPL(NASDAQ)"}), 400

    ticker   = match.group(1)
    exchange = match.group(2)
    # Normalise: store with dot (BRK.B) regardless of whether user typed BRK.B or BRK-B
    ticker   = ticker.replace("-", ".")
    wl       = load_watchlist()

    all_tickers = [e["ticker"].upper() for e in wl.get("us", []) + wl.get("india", [])]
    if ticker in all_tickers:
        return jsonify({"ok": False, "error": f"{ticker} already in watchlist"}), 400

    print(f"\nFetching: {ticker} ({exchange})")
    session, crumb = get_session_and_crumb()
    if not session:
        return jsonify({"ok": False, "error": "Could not connect to Yahoo Finance"}), 500

    result = fetch_quote(ticker, exchange, session, crumb)
    if not result["ok"]:
        return jsonify({"ok": False, "error": result["error"]}), 400

    # Persist to watchlist (permanent)
    wl_entry = {
        "ticker":   ticker,
        "exchange": exchange,
        "notes":    "",
        "order":    len(wl.get(section, [])),
    }
    wl.setdefault(section, []).append(wl_entry)
    save_watchlist(wl)

    # Persist to cache (prices)
    cache = load_cache()
    cache[ticker] = {k: v for k, v in result.items() if k != "ok"}
    save_cache(cache)

    stock = merge(wl_entry, cache)
    print(f"  Saved: {ticker} @ ${stock['price']}  P/E={stock['pe']}")
    return jsonify({"ok": True, "stock": stock})


@app.route("/api/delete", methods=["DELETE"])
def delete_stock():
    body    = request.get_json()
    ticker  = body.get("ticker", "").upper()
    section = body.get("section", "us")
    wl      = load_watchlist()
    before  = len(wl.get(section, []))

    wl[section] = [e for e in wl.get(section, []) if e["ticker"].upper() != ticker]
    if len(wl[section]) < before:
        save_watchlist(wl)
        cache = load_cache()
        cache.pop(ticker, None)
        save_cache(cache)
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "Ticker not found"}), 404


@app.route("/api/note", methods=["PATCH"])
def update_note():
    body    = request.get_json()
    ticker  = body.get("ticker", "").upper()
    note    = body.get("note", "")
    section = body.get("section", "us")
    wl      = load_watchlist()

    for entry in wl.get(section, []):
        if entry["ticker"].upper() == ticker:
            entry["notes"] = note
            save_watchlist(wl)
            return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "Ticker not found"}), 404


@app.route("/api/reorder", methods=["POST"])
def reorder():
    body      = request.get_json()
    section   = body.get("section", "us")
    new_order = body.get("order", [])
    wl        = load_watchlist()

    entry_map  = {e["ticker"].upper(): e for e in wl.get(section, [])}
    wl[section] = [entry_map[t.upper()] for t in new_order if t.upper() in entry_map]
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


# ── UI preferences (column order + visibility) ────────────────

@app.route("/api/prefs", methods=["GET"])
def get_prefs():
    return jsonify(load_prefs())


@app.route("/api/prefs", methods=["PUT"])
def put_prefs():
    """
    Merge-update preferences. Body: { section, order?, visible? }
    where visible is a partial map { colId: bool }.
    """
    body    = request.get_json() or {}
    section = body.get("section")
    if section not in ("us", "india"):
        return jsonify({"ok": False, "error": "Invalid section"}), 400

    prefs = load_prefs()
    current = prefs.get(section) or {}
    order   = current.get("order")
    visible = current.get("visible") or {}

    if isinstance(body.get("order"), list):
        order = [str(x) for x in body["order"]]
    if isinstance(body.get("visible"), dict):
        for k, v in body["visible"].items():
            visible[str(k)] = bool(v)

    prefs[section] = {"order": order or [], "visible": visible}
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
    Compute return % for every ticker in a section between `from` and `to`.
    Body: { section, from: 'YYYY-MM-DD', to: 'YYYY-MM-DD' }
    Returns: { "TICKER": { start, end, return_pct, high, low } | null, ... }
    """
    body    = request.get_json() or {}
    section = body.get("section", "us")
    from_ts = _parse_iso_date(body.get("from"))
    to_ts   = _parse_iso_date(body.get("to"))
    if from_ts is None or to_ts is None or from_ts >= to_ts:
        return jsonify({"ok": False, "error": "Invalid date range"}), 400

    wl      = load_watchlist()
    entries = wl.get(section, [])

    session, crumb = get_session_and_crumb()
    out = {}
    for entry in entries:
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
    """
    ticker  = ticker.upper()
    section = request.args.get("section", "us")
    from_ts = _parse_iso_date(request.args.get("from"))
    to_ts   = _parse_iso_date(request.args.get("to"))
    if from_ts is None or to_ts is None or from_ts >= to_ts:
        return jsonify({"ok": False, "error": "Invalid date range"}), 400

    wl    = load_watchlist()
    entry = next(
        (e for e in wl.get(section, []) if e["ticker"].upper() == ticker),
        None,
    )
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
