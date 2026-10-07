"""Pure-pandas loaders for the source .xls files.

No Streamlit imports here on purpose: this module is shared between the
Streamlit app (which wraps these calls in st.cache_data) and
archive_snapshot.py, which runs standalone from Task Scheduler.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

MARGIN_FILENAME = "MarginNowV2.xls"
FINANCIAL_SUMMARY_FILENAME = "FinancialSummary.xls"
EQUITY_MONITOR_GLOB = "Propriety_Account_Equity_Monitor_V2*.xls"

IDENTIFIER_COLUMNS = {"AE Code", "Client Grp", "Client_No", "Account_Type"}
FINANCIAL_SUMMARY_IDENTIFIER_COLUMNS = {
    "Report_Date", "Client_No", "Curr_Cd", "Origin_Client_No", "AcctType",
}


def margin_now_path(base_dir: Path = DATA_DIR) -> Path:
    return base_dir / MARGIN_FILENAME


def financial_summary_path(base_dir: Path = DATA_DIR) -> Path:
    return base_dir / FINANCIAL_SUMMARY_FILENAME


def latest_equity_monitor_path(base_dir: Path = DATA_DIR) -> Path | None:
    """Return the most recently modified equity-monitor file, or None if absent.

    The report filename carries a timestamp (e.g. ...-202608181300.xls) but a
    new export can be dropped with any name matching the pattern, so we pick
    by file modification time rather than parsing the filename.
    """
    candidates = glob.glob(str(base_dir / EQUITY_MONITOR_GLOB))
    if not candidates:
        return None
    return Path(max(candidates, key=os.path.getmtime))


def _clean_numeric(series: pd.Series) -> pd.Series:
    # pandas >=2.x can infer text columns as its own StringDtype rather than the
    # legacy `object` dtype -- checking `dtype != object` misses that case entirely
    # and skips comma-stripping, silently turning any value >= 1,000 into NaN.
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = series.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def load_margin_now(path: Path | str) -> pd.DataFrame:
    """Load and clean the full account-status snapshot (~10k rows, all accounts)."""
    df = pd.read_excel(path, engine="xlrd")
    for col in df.columns:
        if col not in IDENTIFIER_COLUMNS:
            df[col] = _clean_numeric(df[col])
        else:
            df[col] = df[col].astype(str).str.strip()
    return df


def load_financial_summary(path: Path | str) -> pd.DataFrame:
    """Load the EOD financial summary (~17k rows, all accounts, one date per file).

    Fixed filename, refreshed in place by Jasper -- like MarginNowV2.xls, it
    holds a single point-in-time snapshot rather than history, so the
    snapshot job archives each new Report_Date into eod_snapshots to build
    a daily history over time. Header is on the second row (row index 1);
    the first row is just a "Financial Summary" title.
    """
    df = pd.read_excel(path, engine="xlrd", header=1)
    for col in df.columns:
        if col in FINANCIAL_SUMMARY_IDENTIFIER_COLUMNS:
            df[col] = df[col].astype(str).str.strip()
        else:
            df[col] = _clean_numeric(df[col])
    return df


def load_adjustment_file(path: Path | str) -> pd.DataFrame:
    """Load the monthly adjustment report -> DataFrame[report_date, client_no, amount].

    Columns are matched by name, ignoring case/underscores/spaces (Report_Date, Client_No,
    Adjustments). The header row is found automatically in case the report has a title block.
    If a Curr_Cd column is present, only the base-SGD rows are kept, matching the Excel monitor.
    """
    raw = pd.read_excel(path, engine="xlrd" if str(path).lower().endswith(".xls") else None, header=None)

    def norm(value) -> str:
        return "".join(ch for ch in str(value).lower() if ch.isalnum())

    header_row = next(
        (i for i in range(min(10, len(raw))) if {"clientno", "reportdate"} <= {norm(v) for v in raw.iloc[i]}),
        None,
    )
    if header_row is None:
        raise ValueError("couldn't find a header row containing Report_Date and Client_No")
    df = raw.iloc[header_row + 1:].copy()
    df.columns = [norm(v) for v in raw.iloc[header_row]]
    amount_col = next((c for c in df.columns if c in ("adjustments", "adjustment", "amount")), None)
    if amount_col is None:
        raise ValueError("couldn't find an Adjustments column")
    if "currcd" in df.columns:
        df = df[df["currcd"].astype(str).str.upper().str.contains("SGD")]
    out = pd.DataFrame({
        "report_date": df["reportdate"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip(),
        "client_no": df["clientno"].astype(str).str.strip(),
        "amount": _clean_numeric(df[amount_col]),
    })
    return out.dropna(subset=["amount"]).reset_index(drop=True)


@dataclass
class EquityMonitorReport:
    data: pd.DataFrame
    report_timestamp: pd.Timestamp | None
    source_path: Path


def load_equity_monitor(path: Path | str) -> EquityMonitorReport:
    """Load the prop-account position-ratio report.

    Layout: title row, a row holding the report timestamp in column index 7,
    a page-footer row, then a header row, then data rows.
    """
    path = Path(path)
    raw_head = pd.read_excel(path, engine="xlrd", header=None, nrows=3)
    report_timestamp = None
    if raw_head.shape[1] > 7:
        ts_candidate = raw_head.iat[1, 7]
        if pd.notna(ts_candidate):
            report_timestamp = pd.Timestamp(ts_candidate)

    df = pd.read_excel(path, engine="xlrd", header=3)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    df["Client_No"] = df["Client_No"].astype(str).str.strip()
    for col in ("Position_Ratio", "Net_Positions_Alert", "intra_lots"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Breach" in df.columns:
        df["Breach"] = df["Breach"].astype(str).str.strip()

    if report_timestamp is None:
        report_timestamp = pd.Timestamp(os.path.getmtime(path), unit="s")

    return EquityMonitorReport(data=df, report_timestamp=report_timestamp, source_path=path)


def build_prop_snapshot(base_dir: Path = DATA_DIR) -> tuple[pd.DataFrame, pd.Timestamp, Path] | None:
    """Join the prop-account list (equity monitor) with live figures (margin now).

    Returns (merged_df, report_timestamp, equity_monitor_path) or None if the
    equity monitor file is missing.
    """
    eq_path = latest_equity_monitor_path(base_dir)
    if eq_path is None:
        return None

    report = load_equity_monitor(eq_path)
    margin = load_margin_now(margin_now_path(base_dir))

    keep_cols = [
        "Client_No",
        "AE Code",
        "Client Grp",
        "Account_Type",
        "Equity_Bal",
        "Adj. Equity Bal with Coll.",
        "NLV",
        "IM",
        "MM",
        "Margin Ratio (%)",
        "Ledger_Bal",
        "UPLVal",
    ]
    keep_cols = [c for c in keep_cols if c in margin.columns]
    merged = report.data.merge(margin[keep_cols], on="Client_No", how="left")

    return merged, report.report_timestamp, eq_path
