"""Gunicorn hooks — same startup refresh as `python app.py`."""


def post_worker_init(worker):
    from core.yahoo import refresh_all_prices_async

    refresh_all_prices_async()
