#!/usr/bin/env python3
"""Check HTTPS to PyPI from the host vs inside a one-off python:3.12-slim container.

Use when `docker compose build` fails at `pip install` with SSLCertVerificationError.
Prints a short summary to stdout. Optional: --ndjson-log PATH appends one JSON object per line.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _ndjson(path: Path | None, obj: dict) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _probe(url: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "stock-watchlist-ssl-debug/1"}
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            return True, f"OK HTTP {code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:500]}"


def _docker_probe() -> tuple[bool, str]:
    cmd = [
        "docker",
        "run",
        "--rm",
        "python:3.12-slim",
        "python",
        "-c",
        "import urllib.request; urllib.request.urlopen('https://files.pythonhosted.org/', timeout=20)",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return True, "OK"
        tail = (r.stderr or "")[-600:]
        return False, f"exit {r.returncode}: {tail}"
    except FileNotFoundError:
        return False, "docker CLI not found"
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose host vs Docker TLS to PyPI")
    ap.add_argument(
        "--ndjson-log",
        type=Path,
        metavar="PATH",
        help="Append one JSON line per check (for automation)",
    )
    args = ap.parse_args()
    log = args.ndjson_log

    ts = int(time.time() * 1000)
    proxy = bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
    print(f"HTTPS_PROXY set: {proxy}")
    _ndjson(log, {"ts": ts, "step": "start", "https_proxy_set": proxy})

    ok1, msg1 = _probe("https://files.pythonhosted.org/")
    print(f"  Host -> files.pythonhosted.org : {'PASS' if ok1 else 'FAIL'} — {msg1}")
    _ndjson(log, {"ts": ts, "step": "host_files_pythonhosted", "ok": ok1, "detail": msg1})

    ok2, msg2 = _probe("https://pypi.org/simple/")
    print(f"  Host -> pypi.org/simple       : {'PASS' if ok2 else 'FAIL'} — {msg2}")
    _ndjson(log, {"ts": ts, "step": "host_pypi", "ok": ok2, "detail": msg2})

    ok3, msg3 = _docker_probe()
    print(f"  Docker python:3.12-slim      : {'PASS' if ok3 else 'FAIL'} — {msg3[:400]}")
    _ndjson(log, {"ts": ts, "step": "docker_slim_https", "ok": ok3, "detail": msg3[:800]})

    if ok1 and ok2 and not ok3:
        print()
        print("Typical pattern: host trusts your network; Linux containers do not (TLS inspection).")
        print("Build with:")
        print('  docker compose build --build-arg BUILD_PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org"')
        print("Compose defaults that value; override in .env if needed (see .env.example).")

    _ndjson(log, {"ts": ts, "step": "end"})
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
