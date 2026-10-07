"""Once-a-day EOD refresh: FinancialSummary + monthly adjustment file from Jasper.

FinancialSummary and the adjustment report need a trade date, and that date only
rolls over after the morning EOD process (~9-10am). Downloading them every few
minutes therefore produced empty/wrong-date files from midnight until then. This
job is scheduled separately (from ~10:30, retrying until it succeeds), and it only
accepts a download that really contains a new trade date. Everything else keeps
using the EOD history already archived in SQLite, so the dashboard always shows the
latest *good* EOD data until the next day's file arrives. No Streamlit imports.
"""

from __future__ import annotations

import logging
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from utils import archive_db, data_loader, jasper_downloader
from utils.snapshot_runner import _jasper_configured

log = logging.getLogger(__name__)

STATE_KEY = "last_eod_success_date"
DEFAULT_ADJUSTMENT_FILE = "ProprietoryMonitoring.xls"

# The EOD process follows the US market close, so in Singapore time it finishes an hour
# earlier while US daylight saving is in effect. Task Scheduler can't follow another
# timezone's DST, so the task is scheduled from the earlier time all year and the job
# itself waits until the right local start time for the current season.
DEFAULT_START_DST = "09:30"
DEFAULT_START_STANDARD = "10:30"
DEFAULT_US_TIMEZONE = "America/New_York"
DEFAULT_LOCAL_TIMEZONE = "Asia/Singapore"


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def earliest_start(now: pd.Timestamp, config: dict | None = None) -> time:
    """Local time of day before which an EOD download isn't attempted (depends on US DST)."""
    config = config or {}
    local = now.tz_localize(config.get("local_timezone", DEFAULT_LOCAL_TIMEZONE)) if now.tzinfo is None else now
    us_time = local.tz_convert(ZoneInfo(config.get("eod_timezone", DEFAULT_US_TIMEZONE)))
    us_dst = bool(us_time.dst())
    return _parse_hhmm(
        config.get("eod_start_dst", DEFAULT_START_DST) if us_dst
        else config.get("eod_start_standard", DEFAULT_START_STANDARD)
    )


def _financial_summary_ready(fs_df: pd.DataFrame, today: pd.Timestamp) -> tuple[bool, str]:
    if fs_df.empty:
        return False, "FinancialSummary is empty (EOD process probably not finished yet)"
    dates = pd.to_datetime(fs_df["Report_Date"], format="%Y%m%d", errors="coerce").dropna()
    if dates.empty:
        return False, "FinancialSummary has no valid Report_Date"
    if dates.max() > today.normalize():
        return False, f"FinancialSummary Report_Date {dates.max():%Y-%m-%d} is in the future"
    equity = pd.to_numeric(fs_df["Equity_Collateral_Marginable_Securities"], errors="coerce").fillna(0)
    if (equity == 0).all():
        return False, "FinancialSummary equity is all zero"
    return True, ""


def run_eod_refresh(data_dir: Path, now: pd.Timestamp | None = None, force: bool = False,
                    db_path: Path = archive_db.DB_PATH) -> dict:
    """Returns {"status": ..., "message": ..., "eod_rows": int, "adjustment_rows": int}.

    status is one of: ok, already_done, too_early, not_ready, no_new_date, download_failed, error.
    Only "ok" marks today as done, so a scheduled retry loop stops after the first success.
    """
    now = now or pd.Timestamp.now()
    today = now.strftime("%Y-%m-%d")
    result = {"status": "error", "message": "", "eod_rows": 0, "adjustment_rows": 0}

    if not force and archive_db.get_state(STATE_KEY, db_path=db_path) == today:
        result.update(status="already_done", message=f"EOD data already refreshed on {today}")
        return result

    try:
        config = jasper_downloader.load_config()
    except FileNotFoundError:
        config = None

    start = earliest_start(now, config)
    if not force and now.time() < start:
        result.update(status="too_early", message=f"Before today's EOD start time {start:%H:%M} (US DST-dependent)")
        return result

    if config is not None and _jasper_configured(config):
        success, output = jasper_downloader.download_reports(keyword_set="eod")
        if not success:
            result.update(status="download_failed", message=f"Jasper EOD download failed: {output.strip()[:300]}")
            return result
    else:
        result["message"] = "Jasper not configured -- using whatever files are already in data/. "

    fs_path = data_loader.financial_summary_path(data_dir)
    if not fs_path.exists():
        result.update(status="error", message=f"No {data_loader.FINANCIAL_SUMMARY_FILENAME} in {data_dir}")
        return result
    fs_df = data_loader.load_financial_summary(fs_path)
    ready, why = _financial_summary_ready(fs_df, now)
    if not ready:
        result.update(status="not_ready", message=why)
        return result

    prop = data_loader.build_prop_snapshot(data_dir)
    if prop is None:
        result.update(status="error", message="No equity monitor / MarginNowV2 file to take the prop account list from")
        return result
    client_nos = prop[0]["Client_No"].tolist()

    result["eod_rows"] = archive_db.archive_eod_if_new(fs_df, client_nos, db_path=db_path)

    # The adjustment file is stored per report date regardless of whether it is the month's
    # first trading day; archive_db.load_adjustments_map() only ever uses the first one.
    adj_path = data_dir / (config or {}).get("adjustment_file", DEFAULT_ADJUSTMENT_FILE)
    if adj_path.exists():
        try:
            adj_df = data_loader.load_adjustment_file(adj_path)
            result["adjustment_rows"] = archive_db.save_daily_adjustments(adj_df, client_nos, db_path=db_path)
        except Exception as exc:  # noqa: BLE001 -- a bad adjustment file must not block the EOD archive
            result["message"] += f"Could not read adjustment file ({exc}). "

    if result["eod_rows"] == 0:
        result.update(status="no_new_date", message=result["message"] + "FinancialSummary has no new trade date yet")
        return result

    archive_db.set_state(STATE_KEY, today, db_path=db_path)
    result["status"] = "ok"
    return result
