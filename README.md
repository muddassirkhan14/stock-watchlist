# Stock Watchlist

A local web app to track US and Indian stocks with live prices from Yahoo Finance.

## Features
- Add stocks manually via `TICKER(EXCHANGE)` format — e.g. `AAPL(NASDAQ)` or `TCS(NSE)`
- Separate tabs for US & Global and India stocks
- Live price, day change, 52W high/low, P/E ratio
- Sector and industry grouping
- Sortable columns, drag-to-reorder rows
- Editable notes per stock
- TradingView and Yahoo Finance links per ticker
- Prices refresh automatically on every startup

## Project Structure

```
stock-watchlist/
├── app.py              ← Flask routes
├── requirements.txt
├── core/
│   ├── models.py       ← data helpers (merge, safe_float)
│   ├── storage.py      ← read/write watchlist.json & cache.json
│   └── yahoo.py        ← Yahoo Finance API calls
├── data/
│   └── watchlist.json  ← your stocks & notes (permanent)
└── frontend/
    └── index.html      ← single-page UI
```

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:7080 in your browser.

## Adding Stocks

Type in the input box using the format `TICKER(EXCHANGE)`:

| Exchange | Example |
|----------|---------|
| NYSE     | `BAC(NYSE)` |
| NASDAQ   | `AAPL(NASDAQ)` |
| NSE      | `TCS(NSE)` |
| BSE      | `INFY(BSE)` |
| LSE      | `BP(LSE)` |
| OTC      | `SSUMY(OTC)` |

## Data Files

- `data/watchlist.json` — your stocks, notes, and row order. Committed to git.
- `data/cache.json` — live prices fetched on startup. Ignored by git (regenerated each run).
