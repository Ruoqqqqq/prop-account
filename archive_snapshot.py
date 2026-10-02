"""Snapshot the current prop-account status into the local SQLite archive.

The "Proprietary Accounts Check" emails arrive at 11:00, 13:00, 15:00,
19:00, 22:00, 02:00, and 05:00 daily. Schedule this script to run a few
minutes after each of those times (see README.md) so delivery delay
doesn't cause a run to fetch stale data. Safe to run manually at any time.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from utils.snapshot_runner import run_snapshot

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_PATH = DATA_DIR / "archive_log.txt"

DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("archive_snapshot")


def run() -> int:
    result = run_snapshot(DATA_DIR)

    if not result["fetched_email"]:
        log.warning("Could not fetch a new report from Outlook; used the newest file already in %s", DATA_DIR)

    for error in result["errors"]:
        log.error(error)

    if result["eq_source"] is not None:
        log.info(
            "Archived %d prop account rows, %d new EOD rows, %d active alert(s) (source=%s, report_ts=%s)",
            result["prop_rows"], result["eod_rows"], result["alert_count"], result["eq_source"], result["report_ts"],
        )

    return 1 if result["errors"] and result["eq_source"] is None else 0


if __name__ == "__main__":
    sys.exit(run())
