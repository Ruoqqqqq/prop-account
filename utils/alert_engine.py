"""Computes current Prop-accounts alert conditions (breach + P&L threshold)
and syncs them into the alert log (archive_db.sync_alerts).

Shared between the Prop accounts page (called on every page load) and the
scheduled snapshot job (snapshot_runner.py), so the alert history stays
current even when nobody has the page open. No Streamlit imports on
purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from utils import archive_db, data_loader, fx, pnl_thresholds


@dataclass
class PropStatus:
    merged: pd.DataFrame
    report_ts: pd.Timestamp
    eq_path: Path
    client_nos: list[str]
    eod_history: pd.DataFrame
    testing_periods_df: pd.DataFrame


def build_prop_status(data_dir: Path) -> PropStatus | None:
    """Live equity + P&L + breach + testing status for every prop account,
    or None if no equity monitor / MarginNowV2 file is available yet."""
    result = data_loader.build_prop_snapshot(data_dir)
    if result is None:
        return None
    live, report_ts, eq_path = result
    client_nos = live["Client_No"].tolist()

    eod_history = archive_db.load_eod_history()
    eod_history = eod_history[eod_history["client_no"].isin(client_nos)]

    testing_periods_df = archive_db.load_testing_periods()
    testing_periods_df = testing_periods_df[testing_periods_df["client_no"].isin(client_nos)]

    live_equity = live[["Client_No", "Adj. Equity Bal with Coll."]].rename(
        columns={"Client_No": "client_no", "Adj. Equity Bal with Coll.": "equity_bal"}
    )
    # Static credit excess sits in MarginNowV2's live balance but not in the EOD balance,
    # so take it off here -- otherwise every intraday/monthly P&L is skewed by that amount.
    credit_excess = archive_db.load_credit_excess_map()
    live_equity["equity_bal"] = live_equity["equity_bal"] - live_equity["client_no"].map(credit_excess).fillna(0)
    current_month = pd.Timestamp.now().strftime("%Y-%m")
    adjustments_map = archive_db.load_adjustments_map(current_month)
    now_ts = pd.Timestamp.now()
    pnl = archive_db.compute_eod_pnl(eod_history, live_equity, now_ts, adjustments=adjustments_map)

    merged = live.merge(pnl, left_on="Client_No", right_on="client_no", how="left")
    merged["credit_excess"] = merged["Client_No"].map(credit_excess).fillna(0.0)
    merged["equity_ex_credit"] = merged["Adj. Equity Bal with Coll."] - merged["credit_excess"]
    merged["is_breach"] = merged.get("Breach", pd.Series(dtype=str)).astype(str).str.upper().eq("Y")

    def _testing_label(client_no: str) -> str:
        status = archive_db.testing_status(client_no, testing_periods_df, now_ts)
        if status is None:
            return ""
        state, end_date = status
        return f"Testing until {end_date:%Y-%m-%d}" if state == "active" else f"⚠ Testing ended {end_date:%Y-%m-%d}"

    merged["Testing status"] = merged["Client_No"].map(_testing_label)
    merged["is_active_testing"] = merged["Testing status"].str.startswith("Testing until")
    merged["is_lapsed_testing"] = merged["Testing status"].str.startswith("⚠")
    # A breach covered by an active testing period isn't a real breach. Once the
    # testing period lapses without the breach clearing, it's real again.
    merged["real_breach"] = merged["is_breach"] & ~merged["is_active_testing"]
    merged["breaching_past_testing"] = merged["is_breach"] & merged["is_lapsed_testing"]
    merged["other_real_breach"] = merged["real_breach"] & ~merged["breaching_past_testing"]

    return PropStatus(
        merged=merged, report_ts=report_ts, eq_path=eq_path, client_nos=client_nos,
        eod_history=eod_history, testing_periods_df=testing_periods_df,
    )


def _usd_sgd(usd_sgd: float | None) -> float | None:
    if usd_sgd is not None:
        return usd_sgd
    rate = fx.get_usd_sgd()
    return rate.rate if rate else None


def threshold_sgd(config: dict, client_no: str, metric: str, usd_sgd: float | None) -> float | None:
    """The account's threshold converted from USD (how it is entered) to SGD (how P&L is held)."""
    usd = pnl_thresholds.effective_threshold(config, client_no, metric)
    if usd is None or usd_sgd is None:
        return None
    return usd * usd_sgd


def compute_pnl_alert_hits(merged: pd.DataFrame, config: dict, usd_sgd: float | None = None) -> dict[str, pd.Series]:
    """{metric_col: boolean Series} of which rows are at/below that metric's threshold.

    Thresholds are stored in USD and P&L is in SGD, so each threshold is converted at the
    live USD/SGD rate. With no rate available at all, no P&L alert can be evaluated.
    """
    usd_sgd = _usd_sgd(usd_sgd)
    hits = {}
    for metric_col in pnl_thresholds.METRICS:
        row_thresholds = pd.to_numeric(
            merged["Client_No"].apply(lambda cn: threshold_sgd(config, cn, metric_col, usd_sgd)),
            errors="coerce",
        )
        hits[metric_col] = row_thresholds.notna() & merged[metric_col].notna() & (merged[metric_col] <= row_thresholds)
    return hits


def compute_alerts(merged: pd.DataFrame, config: dict | None = None, usd_sgd: float | None = None) -> list[dict]:
    """Every currently-true alert condition: [{"alert_type", "client_no", "message"}, ...]."""
    config = config or pnl_thresholds.load_config()
    usd_sgd = _usd_sgd(usd_sgd)
    alerts = []
    for _, row in merged.loc[merged["breaching_past_testing"]].iterrows():
        alerts.append({
            "alert_type": "breach_past_testing", "client_no": row["Client_No"],
            "message": "Still breaching after testing period ended",
        })
    for _, row in merged.loc[merged["other_real_breach"]].iterrows():
        alerts.append({
            "alert_type": "breach", "client_no": row["Client_No"],
            "message": "Account flagged as breaching",
        })
    for metric_col, hit in compute_pnl_alert_hits(merged, config, usd_sgd).items():
        for _, row in merged.loc[hit].iterrows():
            usd = pnl_thresholds.effective_threshold(config, row["Client_No"], metric_col)
            alerts.append({
                "alert_type": f"pnl_{metric_col}", "client_no": row["Client_No"],
                "message": (
                    f"{pnl_thresholds.METRICS[metric_col]} SGD {row[metric_col]:,.2f} "
                    f"at or below threshold USD {usd:,.2f} (SGD {usd * usd_sgd:,.2f} @ {usd_sgd:.4f})"
                ),
            })
    return alerts


def sync_prop_alerts(data_dir: Path) -> list[dict]:
    """Recompute prop-account alerts and reconcile them into the alert log.
    Safe to call repeatedly (e.g. every scheduled snapshot run and every
    page load) -- archive_db.sync_alerts dedupes already-active alerts."""
    status = build_prop_status(data_dir)
    if status is None:
        return []
    alerts = compute_alerts(status.merged)
    archive_db.sync_alerts("prop_accounts", alerts)
    return alerts
