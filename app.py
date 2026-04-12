"""
app.py — Flask routes only. Business logic lives in core/.
"""

import re
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from core.models  import merge
from core.storage import load_watchlist, save_watchlist, load_cache, save_cache
from core.yahoo   import fetch_quote, refresh_all_prices, get_session_and_crumb

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
    cache[ticker] = {
        "name":       result["name"],
        "price":      result["price"],
        "change":     result["change"],
        "change_pct": result["change_pct"],
        "high":       result["high"],
        "low":        result["low"],
        "pe":         result["pe"],
        "sector":     result["sector"],
        "industry":   result["industry"],
    }
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
    refresh_all_prices()
    data = get_full_data()
    return jsonify({
        "ok":    True,
        "us":    len(data["us"]),
        "india": len(data["india"]),
    })


# ── Serve frontend ────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    return send_from_directory("frontend", "index.html")


# ── Startup ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Stock Watchlist ===")
    print("Refreshing prices from Yahoo Finance...")
    refresh_all_prices()
    print("\nOpen http://localhost:7080\n")
    app.run(port=7080, debug=False)
