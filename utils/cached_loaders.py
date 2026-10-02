"""Streamlit-cached wrappers around utils.data_loader.

Cache keys include each source file's mtime, so the cache invalidates
automatically whenever the underlying .xls is refreshed, but reruns of the
app in between refreshes reuse the parsed data instead of re-reading a 6MB
.xls file every time a filter widget changes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from utils import data_loader


@st.cache_data(show_spinner="Loading prop account snapshot…")
def _build_prop_snapshot(base_dir_str: str, eq_mtime: float, margin_mtime: float):
    return data_loader.build_prop_snapshot(Path(base_dir_str))


def load_prop_snapshot():
    """Returns (merged_df, report_timestamp, eq_path) or None."""
    eq_path = data_loader.latest_equity_monitor_path()
    if eq_path is None:
        return None
    margin_path = data_loader.margin_now_path()
    if not margin_path.exists():
        return None
    return _build_prop_snapshot(
        str(data_loader.DATA_DIR),
        os.path.getmtime(eq_path),
        os.path.getmtime(margin_path),
    )
