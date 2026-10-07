"""Scheduled once-a-day EOD refresh (FinancialSummary + monthly adjustment file).

Schedule it from ~10:30, repeating every ~20 minutes for a few hours (see
README.md). It is idempotent: once a run has archived a new trade date, later
runs the same day exit immediately without touching Jasper. Runs that find the
EOD process unfinished just log "not ready" and leave the previous EOD data in
place for the next retry.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from utils.eod_runner import run_eod_refresh

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_PATH = DATA_DIR / "eod_refresh_log.txt"

DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("eod_refresh")


def run(force: bool = False) -> int:
    result = run_eod_refresh(DATA_DIR, force=force)
    log.info(
        "EOD refresh: %s -- %s (eod rows %d, adjustment rows %d)",
        result["status"], result["message"].strip(), result["eod_rows"], result["adjustment_rows"],
    )
    return 1 if result["status"] in ("download_failed", "error") else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="run even if today's EOD refresh already succeeded")
    sys.exit(run(force=parser.parse_args().force))
