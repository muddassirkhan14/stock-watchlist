"""
models.py — data helpers: safe_float, merge, exchange suffix map
"""

EXCHANGE_SUFFIX = {
    "NYSE": "", "NASDAQ": "", "OTC": "",
    "NSE":  ".NS", "BSE": ".BO", "LSE": ".L",
    "TSX":  ".TO", "ASX": ".AX", "HKEx": ".HK",
    "SSE":  ".SS", "TYO": ".T",
}


def yf_symbol(ticker: str, exchange: str) -> str:
    """Return the Yahoo Finance symbol for a ticker+exchange pair."""
    # Yahoo Finance uses hyphens for dots in tickers like BRK.B → BRK-B
    yf_ticker = ticker.replace(".", "-")
    return yf_ticker + EXCHANGE_SUFFIX.get(exchange.upper(), "")


def safe_float(val, default: float = 0.0) -> float:
    """Convert val to float, returning default for NaN / Infinity / errors."""
    try:
        v = float(val)
        if v != v or v == float("inf") or v == float("-inf"):
            return default
        return round(v, 2)
    except Exception:
        return default


def merge(wl_entry: dict, cache: dict) -> dict:
    """
    Combine a watchlist entry (permanent) with cached price data (runtime).
    Watchlist fields: ticker, exchange, notes, order
    Cache fields:     name, price, change, change_pct, high, low, pe, sector, industry
    """
    c = cache.get(wl_entry["ticker"], {})
    return {
        "ticker":     wl_entry["ticker"],
        "exchange":   wl_entry["exchange"],
        "notes":      wl_entry.get("notes", ""),
        "order":      wl_entry.get("order", 0),
        "name":       c.get("name",       wl_entry["ticker"]),
        "price":      c.get("price",      0.0),
        "change":     c.get("change",     0.0),
        "change_pct": c.get("change_pct", 0.0),
        "high":       c.get("high",       0.0),
        "low":        c.get("low",        0.0),
        "pe":         c.get("pe",         0.0),
        "sector":     c.get("sector",     "Other"),
        "industry":   c.get("industry",   "Other"),
    }
