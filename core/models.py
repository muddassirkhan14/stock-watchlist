"""
models.py — data helpers: safe_float, merge, exchange suffix map
"""

EXCHANGE_SUFFIX = {
    "NYSE": "", "NASDAQ": "", "OTC": "",
    "NSE":  ".NS", "BSE": ".BO", "LSE": ".L",
    "TSX":  ".TO", "ASX": ".AX", "HKEx": ".HK",
    "SSE":  ".SS", "TYO": ".T",
}

# Yahoo Finance "exchange" codes (as returned by v1/finance/search) → our labels.
# Used to figure out which exchange a US/etc. symbol (no suffix) lives on.
YAHOO_EXCHANGE_CODE = {
    "NMS": "NASDAQ", "NCM": "NASDAQ", "NGM": "NASDAQ",
    "NYQ": "NYSE",   "NYS": "NYSE",
    "PNK": "OTC",    "OBB": "OTC",    "OEM": "OTC",
    "NSI": "NSE",    "BSE": "BSE",
    "LSE": "LSE",    "TOR": "TSX",    "ASX": "ASX",
    "HKG": "HKEx",   "SHH": "SSE",    "JPX": "TYO",
}


def yf_symbol(ticker: str, exchange: str) -> str:
    """Return the Yahoo Finance symbol for a ticker+exchange pair."""
    # Yahoo Finance uses hyphens for dots in tickers like BRK.B → BRK-B
    yf_ticker = ticker.replace(".", "-")
    return yf_ticker + EXCHANGE_SUFFIX.get(exchange.upper(), "")


def reverse_yahoo_symbol(symbol: str, exch_code: str):
    """
    Reverse of yf_symbol: given a Yahoo symbol (e.g. "ARE&M.NS", "AAPL", "BRK-B")
    plus the Yahoo exchange code (e.g. "NSI", "NMS"), return our (ticker, exchange_label)
    pair, or None if the exchange isn't supported.
    """
    if not symbol:
        return None
    # Suffix-bearing exchanges (NSE/BSE/LSE/...): strip the suffix.
    for label, suf in EXCHANGE_SUFFIX.items():
        if suf and symbol.endswith(suf):
            return symbol[:-len(suf)].replace("-", "."), label
    # No suffix → US/OTC; resolve via Yahoo's exchange code.
    label = YAHOO_EXCHANGE_CODE.get((exch_code or "").upper())
    if not label:
        return None
    return symbol.replace("-", "."), label


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
        "ticker":         wl_entry["ticker"],
        "exchange":       wl_entry["exchange"],
        "notes":          wl_entry.get("notes", ""),
        "order":          wl_entry.get("order", 0),
        "name":           c.get("name",           wl_entry["ticker"]),
        "price":          c.get("price",          0.0),
        "change":         c.get("change",         0.0),
        "change_pct":     c.get("change_pct",     0.0),
        "high":           c.get("high",           0.0),
        "low":            c.get("low",            0.0),
        "pe":             c.get("pe",             0.0),
        "sector":         c.get("sector",         "Other"),
        "industry":       c.get("industry",       "Other"),
        # Extended fundamentals (all default to 0 / "" so older caches keep working)
        "market_cap":     c.get("market_cap",     0.0),
        "dividend_yield": c.get("dividend_yield", 0.0),
        "beta":           c.get("beta",           0.0),
        "volume":         c.get("volume",         0.0),
        "avg_volume":     c.get("avg_volume",     0.0),
        "ma50":           c.get("ma50",           0.0),
        "ma200":          c.get("ma200",          0.0),
        "ex_div":         c.get("ex_div",         0),
        "forward_pe":     c.get("forward_pe",     0.0),
        "price_to_sales": c.get("price_to_sales", 0.0),
        "eps":            c.get("eps",            0.0),
        "price_to_book":  c.get("price_to_book",  0.0),
        "peg":            c.get("peg",            0.0),
        "country":        c.get("country",        ""),
        "employees":      c.get("employees",      0),
        "website":        c.get("website",        ""),
        "target_mean":    c.get("target_mean",    0.0),
        "recommendation": c.get("recommendation", ""),
        "debt_equity":    c.get("debt_equity",    0.0),
        "roe":            c.get("roe",            0.0),
        "profit_margin":  c.get("profit_margin",  0.0),
        "earnings_date":  c.get("earnings_date",  0),
    }
