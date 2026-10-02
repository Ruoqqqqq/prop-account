"""Hourly refresh of MarginNowV2.xls / FinancialSummary.xls from Jasper.

Runs independently of archive_snapshot.py's 7x/day schedule (which is tied
to the Outlook position-ratio emails) -- this just re-downloads the two
Jasper-sourced files so they stay fresh through the day. archive_snapshot.py
picks up whatever is newest in data/ each time it runs, so the two jobs
don't need to be synchronized.

Requires jasper_config.json and setup_jasper_password.py to have been set
up already (see README.md); if not configured yet, this exits with an
error rather than silently doing nothing, since being scheduled implies
it's expected to actually run.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from utils import jasper_downloader

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_PATH = DATA_DIR / "jasper_refresh_log.txt"

DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("refresh_jasper_data")


def run() -> int:
    try:
        success, output = jasper_downloader.download_reports()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        log.error("Jasper refresh not configured: %s", exc)
        return 1

    if success:
        log.info("Jasper refresh succeeded")
        return 0

    log.error("Jasper refresh failed: %s", output.strip()[:1000])
    return 1


if __name__ == "__main__":
    sys.exit(run())
