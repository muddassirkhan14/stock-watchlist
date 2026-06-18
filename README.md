# Stock Watchlist

A local web app to track US and Indian stocks with live prices from Yahoo Finance.
<img width="1286" height="306" alt="image" src="https://github.com/user-attachments/assets/4d5edc90-0ce4-447a-b019-a1d8647d7e62" />
<img width="1290" height="423" alt="image" src="https://github.com/user-attachments/assets/ac0004bc-b500-4202-ab8f-dc187e2674c6" />



## Features
- Add stocks manually via `TICKER(EXCHANGE)` format — e.g. `AAPL(NASDAQ)` or `TCS(NSE)`
- Separate tabs for US & Global and India stocks
- Live price, day change, 52W high/low, P/E ratio
- Sector and industry grouping
- Sortable columns, drag-to-reorder rows
- Editable notes per stock
- TradingView and Yahoo Finance links per ticker
- Prices refresh automatically on every startup
- Sub-list management (create, rename, delete per section)
- Extended Yahoo Finance fields (market cap, beta, EPS, ROE, etc.)
- Column show/hide and drag-to-reorder with prefs persistence
- Search-by-name autocomplete via Yahoo search API
- Gunicorn config with background price refresh on worker init
- Highlight watchlist rows with earnings in the next 5 days

## Project Structure

```
stock-watchlist/
├── app.py                 ← Flask routes
├── docker-entrypoint.sh   ← optional runtime corporate CA (update-ca-certificates)
├── requirements.txt
├── docker/corp-ca/        ← optional build-time corporate CA files (*.crt / *.pem)
├── certs/                 ← optional runtime-only CA mount (gitignored patterns)
├── core/
│   ├── models.py          ← data helpers (merge, safe_float)
│   ├── storage.py       ← read/write watchlist.json & cache.json
│   └── yahoo.py         ← Yahoo Finance API calls
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

## Data Files

- `data/watchlist.json` — your stocks, notes, and row order. Committed to git.
- `data/cache.json` — live prices fetched on startup. Ignored by git (regenerated each run).
