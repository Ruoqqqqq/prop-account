"""One shared routine for taking a prop-account snapshot.

Used by both archive_snapshot.py (the scheduled job) and the "Run snapshot
now" button on the Prop accounts page, so the two stay in sync: download
MarginNowV2/FinancialSummary from Jasper (if configured), fetch the latest
position-ratio report from Outlook, archive today's EOD financial summary if
it's a date not seen before, and archive an hourly prop snapshot -- but only
when at least one account currently has an active alert (breach or P&L
threshold), to avoid writing a full batch of rows 7x/day when nothing is
wrong. The EOD data and the shared alert log are still kept current every
run regardless.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from utils import alert_engine, archive_db, data_loader, jasper_downloader, outlook_fetcher

_PLACEHOLDER_MARKERS = ("REPLACE_WITH_", "C:\\path\\to\\")


def _jasper_configured(config: dict) -> bool:
    values = [config.get("exe_path", ""), config.get("keyword", ""), *(config.get("keywords") or {}).values()]
    return not any(marker in value for value in values for marker in _PLACEHOLDER_MARKERS)


def run_snapshot(data_dir: Path) -> dict:
    result = {
        "jasper": None,
        "fetched_email": False,
        "prop_rows": 0,
        "eod_rows": 0,
        "eq_source": None,
        "report_ts": None,
        "alert_count": 0,
        "errors": [],
    }

    try:
        jasper_config = jasper_downloader.load_config()
    except FileNotFoundError:
        jasper_config = None

    if jasper_config is not None and _jasper_configured(jasper_config):
        success, output = jasper_downloader.download_reports(keyword_set="live")
        result["jasper"] = success
        if not success:
            result["errors"].append(f"Jasper download failed: {output.strip()[:300]}")
    elif jasper_config is not None:
        result["errors"].append("jasper_config.json still has placeholder values -- skipping automated download")

    # Once the equity monitor comes down with the "live" Jasper keyword there is no email to wait for.
    if jasper_config is not None and jasper_config.get("equity_monitor_via_jasper"):
        result["fetched_email"] = True
    else:
        fetched = outlook_fetcher.fetch_latest_attachment(data_dir)
        result["fetched_email"] = fetched is not None

    prop_result = data_loader.build_prop_snapshot(data_dir)
    if prop_result is None:
        result["errors"].append(f"No equity monitor file found matching {data_loader.EQUITY_MONITOR_GLOB}")
        return result

    merged, report_ts, eq_path = prop_result
    result["eq_source"] = eq_path.name
    result["report_ts"] = report_ts

    fs_path = data_loader.financial_summary_path(data_dir)
    if fs_path.exists():
        fs_df = data_loader.load_financial_summary(fs_path)
        client_nos = merged["Client_No"].tolist()
        result["eod_rows"] = archive_db.archive_eod_if_new(fs_df, client_nos)
    else:
        result["errors"].append(f"No {data_loader.FINANCIAL_SUMMARY_FILENAME} found in {data_dir}")

    # Keep the alert log current even when nobody has the Prop accounts page open,
    # and use its result to decide whether this moment is worth archiving as a
    # snapshot -- only alerting moments are kept, not every routine run.
    alerts = alert_engine.sync_prop_alerts(data_dir)
    result["alert_count"] = len(alerts)

    if alerts:
        captured_at = pd.Timestamp.now()
        result["prop_rows"] = archive_db.insert_snapshot(merged, captured_at, report_ts)

    return result
