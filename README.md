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

## Docker

From the project root:

```bash
docker compose up --build
```

Open http://localhost:7080. Your lists, cache, history, and local prefs live under `./data` on the host (mounted at `/app/data` in the container) so they survive restarts.

The image runs **Gunicorn** with a single worker process (`REFRESH_STATUS` and the background refresh thread stay consistent). Parallel Yahoo fetches are controlled by:

| Variable | Default | Notes |
|----------|---------|--------|
| `REFRESH_WORKERS` | `6` | Number of concurrent quote fetches (capped at 16). Lower to `4` if Yahoo returns rate-limit errors. |

Set in `docker-compose.yml` under `environment`, or pass `-e REFRESH_WORKERS=4` to `docker run`.

### Build-time TLS vs runtime TLS (corporate networks)

These are **different**:

| Phase | What talks to the network | Typical failure |
|-------|---------------------------|-----------------|
| **`docker compose build`** | `pip` → `files.pythonhosted.org` | SSL error during `RUN pip install` |
| **Container running** | `requests` → `finance.yahoo.com`, `query*.finance.yahoo.com` | UI works, but logs show `Session error` / `Could not get Yahoo Finance session` |

`BUILD_PIP_TRUSTED_HOST` only relaxes verification **for pip during the build**. It does **not** affect Python’s HTTPS to Yahoo when the app refreshes prices.

**Defaults:** `docker-compose.yml` passes **`BUILD_PIP_TRUSTED_HOST=pypi.org files.pythonhosted.org`** so `docker compose up --build` succeeds on many SSL-inspected networks without a `.env` file. For full pip TLS verification (typical home or cloud), set **`BUILD_PIP_TRUSTED_HOST=strict`** in `.env` or pass `--build-arg BUILD_PIP_TRUSTED_HOST=strict`.

**Default Docker behaviour (no certificate files needed):** `docker-compose.yml` sets **`YAHOO_SSL_VERIFY=0`**, so Yahoo `requests` skip TLS certificate verification inside the container. That matches many corporate “SSL inspection” setups where you do not have an easy CA file to install. On a normal home or cloud network, set **`YAHOO_SSL_VERIFY=1`** in `.env` or in `docker-compose.yml` for full verification.

**Optional (more secure than verify=0):** install your organisation’s root CA via **`./certs/`** (runtime mount) or **`docker/corp-ca/`** (build-time); then you can use `YAHOO_SSL_VERIFY=1` and rely on `update-ca-certificates` (see `docker/corp-ca/README.txt`).

### Docker build fails: `SSLCertVerificationError` / self-signed certificate (pip only)

Compose already defaults **`BUILD_PIP_TRUSTED_HOST`** for pip; if the build still fails, your network may need a different host list or a real CA trust store.

1. **Trusted-host workaround (pip build only)** — override in `.env` if the default host list is wrong for your proxy, or use one-shot:
   ```bash
   docker compose build --build-arg BUILD_PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org"
   ```

2. **Prefer** installing your corporate root CA (runtime `./certs` and/or build-time `docker/corp-ca/`) so pip and Yahoo both verify normally. Then you can set **`BUILD_PIP_TRUSTED_HOST=strict`** and rely on the system CA bundle.

To see whether TLS fails on the host vs inside a generic Linux container, run:

```bash
python3 scripts/docker_ssl_debug.py
```

Optional: `python3 scripts/docker_ssl_debug.py --ndjson-log /tmp/ssl-debug.ndjson`. See [`.env.example`](.env.example).

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
