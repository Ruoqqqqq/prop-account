"""Configurable, per-account P&L alert thresholds for the Prop accounts page.

Persisted to data/pnl_alert_thresholds.json:
{
    "default": {"intraday_pnl": None, "daily_pnl_prev_day": None, "monthly_pnl": None},
    "accounts": {"<client_no>": {...same shape...}, ...}
}
Each threshold is a loss floor: an account alerts on that metric when its
P&L falls at or below the value. None means no threshold. A per-account
value overrides "default" for that one metric on that one account; accounts
with no override fall back to "default".
"""

from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
THRESHOLDS_PATH = DATA_DIR / "pnl_alert_thresholds.json"

METRICS = {
    "intraday_pnl": "Intraday P&L",
    "daily_pnl_prev_day": "Daily P&L (prev day)",
    "monthly_pnl": "Monthly P&L",
}


def _empty_metrics() -> dict:
    return {key: None for key in METRICS}


def load_config() -> dict:
    if not THRESHOLDS_PATH.exists():
        return {"default": _empty_metrics(), "accounts": {}}
    try:
        saved = json.loads(THRESHOLDS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"default": _empty_metrics(), "accounts": {}}

    default = _empty_metrics()
    default.update({k: v for k, v in saved.get("default", {}).items() if k in METRICS})

    accounts = {}
    for client_no, metrics in saved.get("accounts", {}).items():
        m = _empty_metrics()
        m.update({k: v for k, v in metrics.items() if k in METRICS})
        accounts[client_no] = m

    return {"default": default, "accounts": accounts}


def save_config(config: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THRESHOLDS_PATH.write_text(json.dumps(config, indent=2))


def set_account_thresholds(client_no: str, metrics: dict) -> None:
    """Set (or update) one account's threshold overrides, leaving others untouched."""
    config = load_config()
    config["accounts"][client_no] = {**_empty_metrics(), **metrics}
    save_config(config)


def clear_account_thresholds(client_no: str) -> None:
    config = load_config()
    config["accounts"].pop(client_no, None)
    save_config(config)


def effective_threshold(config: dict, client_no: str, metric: str) -> float | None:
    """The per-account override if set, else the default for that metric."""
    account_value = config.get("accounts", {}).get(client_no, {}).get(metric)
    if account_value is not None:
        return account_value
    return config.get("default", {}).get(metric)
