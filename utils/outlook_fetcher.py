"""Pull the latest prop-account position-ratio report from Outlook.

The "Proprietary Accounts Check" emails land in a folder named
"Prop Accounts Check" (under Inbox) and carry a .xls attachment named
Propriety_Account_Equity_Monitor_V2-<YYYYMMDDHHMM>.xls, alongside a .pdf
copy and, occasionally, unrelated .msg attachments. Emails go out at
11:00, 13:00, 15:00, 19:00, 22:00, 02:00, and 05:00 daily; this is meant
to be run a few minutes after each of those times to allow for delivery
delay (see README.md for the scheduled-task setup).
"""

from __future__ import annotations

import logging
from pathlib import Path

FOLDER_NAME = "Prop Accounts Check"
ATTACHMENT_PREFIX = "Propriety_Account_Equity_Monitor_V2-"
ATTACHMENT_SUFFIX = ".xls"

log = logging.getLogger(__name__)


def _find_folder_by_name(root, name: str):
    """Depth-first search for a folder named `name` across all stores."""
    for store in root.Folders:
        found = _search_folder(store, name)
        if found is not None:
            return found
    return None


def _search_folder(folder, name: str):
    if folder.Name == name:
        return folder
    try:
        subfolders = folder.Folders
    except Exception:
        return None
    for sub in subfolders:
        found = _search_folder(sub, name)
        if found is not None:
            return found
    return None


def _is_report_attachment(filename: str) -> bool:
    return filename.startswith(ATTACHMENT_PREFIX) and filename.lower().endswith(ATTACHMENT_SUFFIX)


def fetch_latest_attachment(data_dir: Path, max_items_scanned: int = 20) -> Path | None:
    """Download the most recent report attachment into data_dir if not already there.

    Returns the local path to the (possibly pre-existing) file, or None if
    Outlook isn't reachable or no matching email/attachment was found.
    """
    try:
        import win32com.client
    except ImportError:
        log.error("pywin32 is not installed; cannot read Outlook. Run: pip install pywin32")
        return None

    try:
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    except Exception as exc:
        log.error("Could not connect to Outlook: %s", exc)
        return None

    folder = _find_folder_by_name(outlook, FOLDER_NAME)
    if folder is None:
        log.error("Outlook folder '%s' not found", FOLDER_NAME)
        return None

    items = folder.Items
    items.Sort("[ReceivedTime]", True)

    scanned = 0
    for item in items:
        if scanned >= max_items_scanned:
            break
        scanned += 1
        try:
            attachments = item.Attachments
        except Exception:
            continue
        for attachment in attachments:
            filename = attachment.FileName
            if not _is_report_attachment(filename):
                continue
            dest = Path(data_dir) / filename
            if dest.exists():
                log.info("Already have %s, skipping download", filename)
                return dest
            data_dir.mkdir(parents=True, exist_ok=True)
            attachment.SaveAsFile(str(dest))
            log.info("Downloaded %s (email received %s)", filename, item.ReceivedTime)
            return dest

    log.warning("No email with a %s*%s attachment found in the last %d items",
                ATTACHMENT_PREFIX, ATTACHMENT_SUFFIX, max_items_scanned)
    return None
