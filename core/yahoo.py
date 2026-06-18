"""
yahoo.py — all Yahoo Finance API interactions:
  - session + crumb setup
  - fetch_quote()        : single stock (used when adding manually)
  - refresh_all_prices() : refresh entire watchlist into cache
  - fetch_history()      : 5y daily closes for a single ticker
  - get_history_cached() : cached wrapper around fetch_history (24h TTL)
  - compute_return()     : return % between two unix timestamps
"""

import os
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import quote

from core.models   import safe_float, yf_symbol, reverse_yahoo_symbol
from core.storage  import (
    load_watchlist, load_cache, save_cache,
    load_history, save_history,
)

HISTORY_TTL_HOURS = 24

# Live progress for the background price refresh kicked off at startup
# (or via /api/refresh). Read by the frontend via /api/status.
REFRESH_STATUS = {
    "running":       False,
    "done":          0,
    "total":         0,
    "current":       None,   # ticker currently being fetched
    "last_finished": None,   # ISO timestamp of the last completed refresh
    "error":         None,
}


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_tls_warn_silenced = False


def _yahoo_ssl_verify() -> bool:
    """
    When False, Yahoo HTTPS calls skip TLS certificate verification.
    Set env YAHOO_SSL_VERIFY=0 on networks that intercept HTTPS (corporate
    proxies) where Linux containers do not trust the inspection CA.
    Default is verify on (1).
    """
    v = os.environ.get("YAHOO_SSL_VERIFY", "1").strip().lower()
    return v not in ("0", "false", "no", "")


def _silence_insecure_tls_warning_once() -> None:
    global _tls_warn_silenced
    if _tls_warn_silenced or _yahoo_ssl_verify():
        return
    _tls_warn_silenced = True
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass


# ── Session ───────────────────────────────────────────────────

def get_session_and_crumb():
    """Return (requests.Session, crumb) or (None, None) on failure."""
    _silence_insecure_tls_warning_once()
    verify = _yahoo_ssl_verify()
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get("https://finance.yahoo.com", timeout=10, verify=verify)
        time.sleep(1)
        r = session.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10, verify=verify
        )
        crumb = r.text.strip()
        if crumb and "<" not in crumb:
            return session, crumb
    except Exception as e:
        print(f"  Session error: {e}")
    return None, None


# ── Search (autocomplete by name) ─────────────────────────────

def search_tickers(q: str, session=None) -> list:
    """
    Hit Yahoo's lightweight search endpoint and return a normalized list
    of candidates: [{ticker, exchange, name, type, section}, ...].
    Results from exchanges we don't map are silently dropped so the rest
    of the add pipeline never sees something it can't handle.
    """
    if not q or len(q.strip()) < 2:
        return []
    _silence_insecure_tls_warning_once()
    verify = _yahoo_ssl_verify()
    sess = session or requests.Session()
    try:
        r = sess.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": q.strip(), "quotesCount": 10, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            verify=verify,
        )
        if r.status_code != 200:
            return []
        quotes = r.json().get("quotes", []) or []
    except Exception:
        return []

    out = []
    for q_ in quotes:
        if q_.get("quoteType") not in ("EQUITY", "ETF", "INDEX", "MUTUALFUND"):
            continue
        rev = reverse_yahoo_symbol(q_.get("symbol", ""), q_.get("exchange", ""))
        if not rev:
            continue
        ticker, exchange = rev
        out.append({
            "ticker":   ticker,
            "exchange": exchange,
            "name":     q_.get("shortname") or q_.get("longname") or ticker,
            "type":     q_.get("quoteType"),
            "section":  "india" if exchange in ("NSE", "BSE") else "us",
        })
    return out


# ── Single quote ──────────────────────────────────────────────

def fetch_quote(ticker: str, exchange: str, session, crumb) -> dict:
    """
    Fetch price, change, 52W range, P/E, sector, industry for one ticker.
    Returns { ok, name, price, change, change_pct, high, low, pe, sector, industry }
    or      { ok: False, error: str }
    """
    symbol = yf_symbol(ticker, exchange)
    sym_url = quote(symbol, safe="")
    verify = _yahoo_ssl_verify()
    try:
        # v8 chart — price + 52W range + day change
        r = session.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_url}",
            params={"range": "5d", "interval": "1d", "crumb": crumb},
            timeout=15,
            verify=verify,
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
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_url}",
                params={"range": "1y", "interval": "1d", "crumb": crumb},
                timeout=15,
                verify=verify,
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

        # v10 quoteSummary — P/E, sector, industry, and extended fundamentals
        pe             = 0.0
        sector         = "Other"
        industry       = "Other"
        market_cap     = 0.0
        dividend_yield = 0.0
        beta           = 0.0
        volume         = 0.0
        avg_volume     = 0.0
        ma50           = 0.0
        ma200          = 0.0
        ex_div         = 0
        forward_pe     = 0.0
        price_to_sales = 0.0
        eps            = 0.0
        price_to_book  = 0.0
        peg            = 0.0
        country        = ""
        employees      = 0
        website        = ""
        target_mean    = 0.0
        recommendation = ""
        debt_equity    = 0.0
        roe            = 0.0
        profit_margin  = 0.0
        earnings_date  = 0

        r2 = session.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym_url}",
            params={
                "modules": ("summaryDetail,defaultKeyStatistics,assetProfile,"
                            "financialData,calendarEvents"),
                "crumb":   crumb,
            },
            timeout=15,
            verify=verify,
        )
        if r2.status_code == 200:
            try:
                summary = r2.json().get("quoteSummary", {}).get("result", [{}])[0]
                sd      = summary.get("summaryDetail",        {}) or {}
                ks      = summary.get("defaultKeyStatistics", {}) or {}
                ap      = summary.get("assetProfile",         {}) or {}
                fd      = summary.get("financialData",        {}) or {}
                ce      = summary.get("calendarEvents",       {}) or {}

                def _raw(d, key):
                    """Yahoo fields are { raw, fmt, longFmt }; return raw or None."""
                    v = d.get(key)
                    if isinstance(v, dict):
                        return v.get("raw")
                    return v

                pe_raw = (
                    _raw(sd, "trailingPE") or
                    _raw(ks, "forwardPE")  or
                    _raw(sd, "forwardPE")  or
                    0.0
                )
                pe       = safe_float(pe_raw)
                sector   = ap.get("sector")   or "Other"
                industry = ap.get("industry") or "Other"

                # summaryDetail
                market_cap     = safe_float(_raw(sd, "marketCap"))
                dividend_yield = safe_float(_raw(sd, "dividendYield"))
                beta           = safe_float(_raw(sd, "beta"))
                volume         = safe_float(_raw(sd, "regularMarketVolume") or _raw(sd, "volume"))
                avg_volume     = safe_float(_raw(sd, "averageVolume10days") or _raw(sd, "averageVolume"))
                ma50           = safe_float(_raw(sd, "fiftyDayAverage"))
                ma200          = safe_float(_raw(sd, "twoHundredDayAverage"))
                ex_div         = int(_raw(sd, "exDividendDate") or 0)
                forward_pe     = safe_float(_raw(sd, "forwardPE"))
                price_to_sales = safe_float(_raw(sd, "priceToSalesTrailing12Months"))

                # defaultKeyStatistics
                eps           = safe_float(_raw(ks, "trailingEps"))
                price_to_book = safe_float(_raw(ks, "priceToBook"))
                peg           = safe_float(_raw(ks, "pegRatio"))

                # assetProfile
                country   = ap.get("country")  or ""
                employees = int(_raw(ap, "fullTimeEmployees") or 0)
                website   = ap.get("website")  or ""

                # financialData
                target_mean    = safe_float(_raw(fd, "targetMeanPrice"))
                recommendation = fd.get("recommendationKey") or ""
                debt_equity    = safe_float(_raw(fd, "debtToEquity"))
                roe            = safe_float(_raw(fd, "returnOnEquity"))
                profit_margin  = safe_float(_raw(fd, "profitMargins"))

                # calendarEvents: earnings.earningsDate is a list of { raw, fmt } dicts
                try:
                    ed = (ce.get("earnings") or {}).get("earningsDate") or []
                    if ed:
                        first = ed[0]
                        earnings_date = int(first.get("raw") if isinstance(first, dict) else first or 0)
                except Exception:
                    earnings_date = 0

            except Exception as e:
                print(f"  Summary parse error for {symbol}: {e}")
        else:
            print(f"  quoteSummary HTTP {r2.status_code} for {symbol}")

        return {
            "ok":             True,
            "name":           name,
            "price":          price,
            "change":         change,
            "change_pct":     change_pct,
            "high":           high52,
            "low":            low52,
            "pe":             pe,
            "sector":         sector,
            "industry":       industry,
            "market_cap":     market_cap,
            "dividend_yield": dividend_yield,
            "beta":           beta,
            "volume":         volume,
            "avg_volume":     avg_volume,
            "ma50":           ma50,
            "ma200":          ma200,
            "ex_div":         ex_div,
            "forward_pe":     forward_pe,
            "price_to_sales": price_to_sales,
            "eps":            eps,
            "price_to_book":  price_to_book,
            "peg":            peg,
            "country":        country,
            "employees":      employees,
            "website":        website,
            "target_mean":    target_mean,
            "recommendation": recommendation,
            "debt_equity":    debt_equity,
            "roe":            roe,
            "profit_margin":  profit_margin,
            "earnings_date":  earnings_date,
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Bulk refresh ──────────────────────────────────────────────

_refresh_lock = threading.Lock()

# Each worker thread gets its own Yahoo session (requests.Session is not thread-safe).
_refresh_tls = threading.local()


def _thread_session_and_crumb():
    """Lazily create one (session, crumb) pair per worker thread."""
    if getattr(_refresh_tls, "ready", False):
        return _refresh_tls.session, _refresh_tls.crumb
    _refresh_tls.ready = True
    session, crumb = get_session_and_crumb()
    _refresh_tls.session, _refresh_tls.crumb = session, crumb
    return session, crumb


def _refresh_one_entry(entry: dict):
    """Fetch one quote; safe to run in parallel across threads (own session per thread)."""
    ticker   = entry["ticker"]
    exchange = entry["exchange"]
    session, crumb = _thread_session_and_crumb()
    if not session:
        return ticker, {"ok": False, "error": "Could not get Yahoo Finance session"}
    return ticker, fetch_quote(ticker, exchange, session, crumb)


def refresh_all_prices() -> None:
    """
    Re-fetch live prices for every ticker in the watchlist and save to cache.
    Notes and order in watchlist.json are never touched.
    Updates REFRESH_STATUS while it runs so the UI can show progress.

    Uses a bounded thread pool (env REFRESH_WORKERS, default 6, max 16) so many
    tickers refresh concurrently; each thread keeps its own Yahoo session.
    """
    wl    = load_watchlist()
    cache = load_cache()

    # Flatten across every sub-list in every section, dedupe by ticker
    # so the same ticker appearing in multiple sub-lists is fetched once.
    seen = set()
    all_entries = []
    for section in wl.values():
        for sub in section.get("lists", []):
            for entry in sub.get("stocks", []):
                t = (entry.get("ticker") or "").upper()
                if t and t not in seen:
                    seen.add(t)
                    all_entries.append(entry)

    REFRESH_STATUS.update({
        "running": True, "done": 0, "total": len(all_entries),
        "current": None, "error": None,
    })

    try:
        if not all_entries:
            print("  Watchlist is empty — nothing to refresh.")
            return

        try:
            workers = int(os.environ.get("REFRESH_WORKERS", "6"))
        except ValueError:
            workers = 6
        workers = max(1, min(workers, 16))

        print(f"  Refreshing {len(all_entries)} tickers with up to {workers} parallel workers…")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(_refresh_one_entry, e): e for e in all_entries}
            for fut in as_completed(future_map):
                entry = future_map[fut]
                ticker = entry["ticker"]
                try:
                    tkr, result = fut.result()
                    ticker = tkr
                except Exception as e:
                    result = {"ok": False, "error": str(e)}

                REFRESH_STATUS["current"] = ticker
                if result.get("ok"):
                    cache[ticker] = {k: v for k, v in result.items() if k != "ok"}
                    sign = "+" if result["change"] >= 0 else ""
                    print(
                        f"  {ticker:10s} ({entry.get('exchange','')})  "
                        f"${result['price']}  "
                        f"{sign}{result['change']} ({sign}{result['change_pct']}%)  "
                        f"P/E={result['pe']}"
                    )
                else:
                    print(f"  {ticker:10s} … FAILED — {result.get('error', 'unknown')}")

                REFRESH_STATUS["done"] += 1

        save_cache(cache)
    finally:
        REFRESH_STATUS["running"]       = False
        REFRESH_STATUS["current"]       = None
        REFRESH_STATUS["last_finished"] = datetime.now().isoformat(timespec="seconds")


def refresh_all_prices_async() -> bool:
    """
    Kick off refresh_all_prices() in a background daemon thread.
    Returns True if a new job was started, False if one is already running.
    """
    if not _refresh_lock.acquire(blocking=False):
        return False
    if REFRESH_STATUS["running"]:
        _refresh_lock.release()
        return False

    def _run():
        try:
            refresh_all_prices()
        finally:
            _refresh_lock.release()

    threading.Thread(target=_run, daemon=True, name="price-refresh").start()
    return True


# ── History (daily closes) ────────────────────────────────────

def fetch_history(ticker: str, exchange: str, session, crumb,
                  range_: str = "5y") -> list:
    """
    Return a list of [unix_ts, close_price] pairs for the given ticker.
    Uses Yahoo's v8 chart endpoint with 1d interval. Returns [] on failure.
    """
    symbol = yf_symbol(ticker, exchange)
    sym_url = quote(symbol, safe="")
    verify = _yahoo_ssl_verify()
    try:
        r = session.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_url}",
            params={"range": range_, "interval": "1d", "crumb": crumb},
            timeout=20,
            verify=verify,
        )
        if r.status_code != 200:
            print(f"  history HTTP {r.status_code} for {symbol}")
            return []

        result = r.json().get("chart", {}).get("result", [])
        if not result:
            return []

        timestamps = result[0].get("timestamp", []) or []
        closes     = (result[0].get("indicators", {})
                              .get("quote", [{}])[0]
                              .get("close", [])) or []

        series = []
        for ts, cl in zip(timestamps, closes):
            if ts is None or cl is None:
                continue
            series.append([int(ts), round(float(cl), 4)])
        return series

    except Exception as e:
        print(f"  history error for {symbol}: {e}")
        return []


def _is_fresh(last_fetched: str) -> bool:
    if not last_fetched:
        return False
    try:
        dt = datetime.fromisoformat(last_fetched)
    except Exception:
        return False
    return (datetime.now() - dt) < timedelta(hours=HISTORY_TTL_HOURS)


def get_history_cached(ticker: str, exchange: str,
                       session=None, crumb=None) -> list:
    """
    Load a ticker's daily close series from history.json, refetching from
    Yahoo if missing or stale. Persists the updated cache.

    If `session`/`crumb` are not provided and a fetch is needed, creates a
    new session on the fly.
    """
    hist  = load_history()
    entry = hist.get(ticker) or {}

    if entry.get("series") and _is_fresh(entry.get("last_fetched")):
        return entry["series"]

    if session is None or crumb is None:
        session, crumb = get_session_and_crumb()
        if not session:
            return entry.get("series", [])

    series = fetch_history(ticker, exchange, session, crumb)
    if series:
        hist[ticker] = {
            "last_fetched": datetime.now().isoformat(timespec="seconds"),
            "series":       series,
        }
        save_history(hist)
        return series

    return entry.get("series", [])


def compute_return(series: list, from_ts: int, to_ts: int):
    """
    Given a [[ts, close], ...] series and a unix timestamp range,
    return { start, end, return_pct, high, low } or None if the range
    doesn't contain at least 2 data points.
    """
    if not series or from_ts >= to_ts:
        return None

    sliced = [(ts, cl) for ts, cl in series if from_ts <= ts <= to_ts]
    if len(sliced) < 2:
        return None

    start = sliced[0][1]
    end   = sliced[-1][1]
    if start <= 0:
        return None

    closes = [cl for _, cl in sliced]
    return {
        "start":      round(start, 2),
        "end":        round(end,   2),
        "return_abs": round(end - start, 2),
        "return_pct": round((end - start) / start * 100, 2),
        "high":       round(max(closes), 2),
        "low":        round(min(closes), 2),
    }
