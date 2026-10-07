"""USD/SGD exchange rate for converting USD P&L thresholds into SGD. No Streamlit imports.

P&L thresholds are entered in USD while every figure from the two reports (and the
credit excess) is in SGD. The rate comes from the public Frankfurter feed (ECB
reference rates, updated each business day). Each good rate is saved to the archive
DB, so if the feed is unreachable the last saved rate is used instead -- alerts keep
working, and the returned `source` says the rate is stale.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from utils import archive_db

FX_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=SGD"
PAIR = "USDSGD"
_CACHE_SECONDS = 900
_TIMEOUT_SECONDS = 5

_cache: dict = {"at": 0.0, "value": None}


@dataclass
class FxRate:
    rate: float
    rate_date: str      # date the feed quotes the rate for
    fetched_at: str     # when this app last got it from the feed
    live: bool          # False -> feed unreachable, showing the last saved rate


def _fetch() -> tuple[float, str]:
    # The feed's CDN rejects urllib's default User-Agent with a 403.
    request = urllib.request.Request(FX_URL, headers={"User-Agent": "nbs-prop-monitor/1.0"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310 -- fixed https URL
        payload = json.loads(resp.read().decode("utf-8"))
    rate = float(payload["rates"]["SGD"])
    if rate <= 0:
        raise ValueError("non-positive rate")
    return rate, str(payload.get("date", ""))


def _save(rate: float, rate_date: str, db_path: Path = archive_db.DB_PATH) -> None:
    conn = archive_db.get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO fx_rates (pair, rate, rate_date, fetched_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(pair) DO UPDATE SET rate=excluded.rate, rate_date=excluded.rate_date, "
            "fetched_at=excluded.fetched_at",
            (PAIR, rate, rate_date, pd.Timestamp.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _load_saved(db_path: Path = archive_db.DB_PATH) -> FxRate | None:
    conn = archive_db.get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT rate, rate_date, fetched_at FROM fx_rates WHERE pair = ?", (PAIR,)
        ).fetchone()
    finally:
        conn.close()
    return FxRate(row[0], row[1], row[2], live=False) if row else None


def get_usd_sgd(force: bool = False) -> FxRate | None:
    """Current USD->SGD rate (1 USD = rate SGD), or None if there has never been one."""
    now = time.time()
    if not force and _cache["value"] is not None and now - _cache["at"] < _CACHE_SECONDS:
        return _cache["value"]
    try:
        rate, rate_date = _fetch()
        _save(rate, rate_date)
        value = FxRate(rate, rate_date, pd.Timestamp.now().isoformat(), live=True)
    except Exception:  # noqa: BLE001 -- any network/parse failure falls back to the saved rate
        value = _load_saved()
    _cache["at"], _cache["value"] = now, value
    return value
