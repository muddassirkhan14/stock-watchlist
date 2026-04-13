"""
yahoo.py — all Yahoo Finance API interactions:
  - session + crumb setup
  - fetch_quote()        : single stock (used when adding manually)
  - refresh_all_prices() : refresh entire watchlist into cache
"""

import time
import requests

from core.models   import safe_float, yf_symbol
from core.storage  import load_watchlist, load_cache, save_cache


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Session ───────────────────────────────────────────────────

def get_session_and_crumb():
    """Return (requests.Session, crumb) or (None, None) on failure."""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get("https://finance.yahoo.com", timeout=10)
        time.sleep(1)
        r = session.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10
        )
        crumb = r.text.strip()
        if crumb and "<" not in crumb:
            return session, crumb
    except Exception as e:
        print(f"  Session error: {e}")
    return None, None


# ── Single quote ──────────────────────────────────────────────

def fetch_quote(ticker: str, exchange: str, session, crumb) -> dict:
    """
    Fetch price, change, 52W range, P/E, sector, industry for one ticker.
    Returns { ok, name, price, change, change_pct, high, low, pe, sector, industry }
    or      { ok: False, error: str }
    """
    symbol = yf_symbol(ticker, exchange)
    try:
        # v8 chart — price + 52W range + day change
        r = session.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d", "crumb": crumb},
            timeout=15,
        )
        if r.status_code == 429:
            return {"ok": False, "error": "Rate limited — wait a minute and try again"}
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code} for {symbol}"}

        result = r.json().get("chart", {}).get("result", [])
        if not result:
            return {"ok": False, "error": f"No data for {symbol}"}

        meta   = result[0].get("meta", {})
        price  = safe_float(meta.get("regularMarketPrice") or meta.get("previousClose"))
        high52 = safe_float(meta.get("fiftyTwoWeekHigh"))
        low52  = safe_float(meta.get("fiftyTwoWeekLow"))
        name   = meta.get("shortName") or meta.get("longName") or ticker

        # Day change — use last two closing prices from 5d OHLC
        closes = [c for c in result[0].get("indicators", {}).get("quote", [{}])[0].get("close", []) if c]
        if len(closes) >= 2:
            prev_close = closes[-2]
            change     = round(price - prev_close, 2)
            change_pct = round((change / prev_close) * 100, 2)
        else:
            change     = 0.0
            change_pct = 0.0

        # 52W high/low — fetch separately with 1y range if not in meta
        if not high52 or not low52:
            r1y = session.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"range": "1y", "interval": "1d", "crumb": crumb},
                timeout=15,
            )
            if r1y.status_code == 200:
                res1y = r1y.json().get("chart", {}).get("result", [])
                if res1y:
                    q     = res1y[0].get("indicators", {}).get("quote", [{}])[0]
                    highs = [h for h in q.get("high", []) if h]
                    lows  = [l for l in q.get("low",  []) if l]
                    if highs: high52 = round(max(highs), 2)
                    if lows:  low52  = round(min(lows),  2)

        time.sleep(0.4)

        # v10 quoteSummary — P/E + sector + industry
        pe = 0.0
        sector   = "Other"
        industry = "Other"

        r2 = session.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
            params={
                "modules": "summaryDetail,defaultKeyStatistics,assetProfile",
                "crumb":   crumb,
            },
            timeout=15,
        )
        if r2.status_code == 200:
            try:
                summary  = r2.json().get("quoteSummary", {}).get("result", [{}])[0]
                sd       = summary.get("summaryDetail", {})
                ks       = summary.get("defaultKeyStatistics", {})
                ap       = summary.get("assetProfile", {})
                pe_raw   = (
                    (sd.get("trailingPE") or {}).get("raw") or
                    (ks.get("forwardPE")  or {}).get("raw") or
                    (sd.get("forwardPE")  or {}).get("raw") or
                    0.0
                )
                pe       = safe_float(pe_raw)
                sector   = ap.get("sector")   or "Other"
                industry = ap.get("industry") or "Other"
            except Exception as e:
                print(f"  Summary parse error for {symbol}: {e}")
        else:
            print(f"  quoteSummary HTTP {r2.status_code} for {symbol}")

        return {
            "ok":         True,
            "name":       name,
            "price":      price,
            "change":     change,
            "change_pct": change_pct,
            "high":       high52,
            "low":        low52,
            "pe":         pe,
            "sector":     sector,
            "industry":   industry,
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Bulk refresh ──────────────────────────────────────────────

def refresh_all_prices() -> None:
    """
    Re-fetch live prices for every ticker in the watchlist and save to cache.
    Notes and order in watchlist.json are never touched.
    """
    wl          = load_watchlist()
    cache       = load_cache()
    all_entries = wl.get("us", []) + wl.get("india", [])

    if not all_entries:
        print("  Watchlist is empty — nothing to refresh.")
        return

    print("  Getting Yahoo Finance session...")
    session, crumb = get_session_and_crumb()
    if not session:
        print("  Could not get session — skipping price refresh.")
        return

    for entry in all_entries:
        ticker   = entry["ticker"]
        exchange = entry["exchange"]
        print(f"  {ticker:10s} ({exchange}) ...", end=" ", flush=True)

        result = fetch_quote(ticker, exchange, session, crumb)
        if result["ok"]:
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
            sign = "+" if result["change"] >= 0 else ""
            print(
                f"${result['price']}  "
                f"{sign}{result['change']} ({sign}{result['change_pct']}%)  "
                f"P/E={result['pe']}"
            )
        else:
            print(f"FAILED — {result['error']}")

        time.sleep(1.5)

    save_cache(cache)
