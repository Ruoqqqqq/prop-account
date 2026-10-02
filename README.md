# NBS account monitor

Streamlit dashboard for monitoring account status and prop accounts, with archiving of prop account snapshots pulled from Outlook and Jasper.

## Data files (`data/`)

All input and generated data lives in `data/`, separate from the app code:

- `data/MarginNowV2.xls` — full account universe (~10k rows), live intraday margin/equity figures. Fixed filename, refreshed in place **by Jasper** (manually, or automatically if you set up the Jasper exe automation below).
- `data/FinancialSummary.xls` — full account universe (~17k rows), **EOD** financial summary (`Client_No`, `Report_Date`, `TotalEquity`, and other margin/collateral fields). Fixed filename, refreshed in place **by Jasper** once a day; holds a single date's snapshot, not history — the snapshot job archives each new `Report_Date` into `eod_snapshots` to build a daily history over time.
- `data/Propriety_Account_Equity_Monitor_V2-*.xls` — the prop account list (`Client_No`) plus `Position_Ratio`, `intra_lots`, and `Breach`. Downloaded automatically **from Outlook** (see below). The app always reads whichever matching file was modified most recently.
- `data/prop_archive.db` — SQLite archive: hourly-ish prop snapshots, daily EOD equity per account, and remarks/screenshots.
- `data/attachments/` — screenshot/proof files uploaded through Remarks and Testing periods.
- `data/archive_log.txt` — log output from `archive_snapshot.py`.

## Pages

1. **Prop accounts** — the prop account list from the equity monitor file, joined with live equity/margin figures, showing position ratio, breach flags, and P&L (see below). Includes a manual "Run snapshot now" button, **Backfill historical EOD data** and **Monthly equity adjustments** uploaders, a **Testing periods** section (with edit/delete and approval-proof upload), and a **Remarks & supporting documents** section for logging notes and screenshots against a breach.
2. **Archive history** — browse everything the archive job has recorded, one row per snapshot batch with a per-batch CSV download (account number, position ratio, intra lots, breach, and P&L), filter by account/date range, chart position ratio over time.
3. **Settings** — credit excess (excluded from the live balance), P&L alert thresholds, monthly equity adjustments, testing periods and EOD backfill. Anyone can view; changing anything needs the admin password (see below).

## Prop account P&L definitions

The equity figures used everywhere on the Prop accounts page:
- **Live equity** — `MarginNowV2.xls`, column `Adj. Equity Bal with Coll.`
- **EOD equity** — `FinancialSummary.xls`, column `Equity_Collateral_Marginable_Securities`, archived once per new `Report_Date` into `eod_snapshots`

All three P&L figures are anchored to `eod_snapshots`, not the intraday archive, since EOD figures from Jasper are the authoritative daily close:

- **Intraday P&L** — live equity (right now) minus the most recently archived EOD close.
- **Daily P&L (previous day)** — the last archived EOD close minus the one before it. A fixed, non-moving figure for "how much was made/lost yesterday." Shows `—` until at least two EOD dates have been archived.
- **Monthly P&L** — live equity (right now) minus the EOD close from just before this month started (month-to-date).

Because `eod_snapshots` only grows when a *new* `Report_Date` shows up in `FinancialSummary.xls`, these figures build up gradually — daily P&L (previous day) needs a second day archived before it shows a number instead of `—`. Intraday and monthly P&L both need a non-blank *live* equity figure too (`Adj. Equity Bal with Coll.` in `MarginNowV2.xls`); as of the pandas-dtype fix below, that's populated for all prop accounts present in `MarginNowV2.xls` (`BBB1188` is the one account genuinely absent from that file).

Monthly P&L also subtracts that month's saved equity adjustment for the account (deposits/withdrawals/corrections aren't trading P&L) — see "Monthly equity adjustments" on the Prop accounts page.

**pandas dtype bug (fixed):** pandas ≥2.x can infer text columns as its own `StringDtype` rather than the legacy `object` dtype. `data_loader._clean_numeric()` used to check `dtype != object`, which missed that case and skipped comma-stripping — silently turning any value ≥ 1,000 into `NaN` across `MarginNowV2.xls`. Fixed by checking `pd.api.types.is_numeric_dtype()` instead. If numbers ever look suspiciously blank/small again, check for a similar dtype-check bug before assuming it's a real data gap.

## Running the dashboard

```bash
streamlit run streamlit_app.py
```

## Position ratio data comes from Outlook

The "Proprietary Accounts Check" emails land in the Outlook folder **Prop Accounts Check** (under Inbox, in the `ruoqingyuan@phillip.com.sg` mailbox) and carry a `.xls` attachment named `Propriety_Account_Equity_Monitor_V2-<YYYYMMDDHHMM>.xls`. `archive_snapshot.py` connects to the desktop Outlook client via COM (`pywin32`), finds the most recent email in that folder with a matching attachment, and saves it into `data/` if it isn't already there.

This means:
- Outlook must be open and signed in to that mailbox on the machine running the scheduled task.
- If no new email has arrived yet, the script logs a warning and falls back to archiving from whatever report is already the newest file in `data/`.

`MarginNowV2.xls` and `FinancialSummary.xls` can optionally be pulled automatically from Jasper too — see the next section. Until that's set up, they're refreshed in place by whatever process/schedule already drops them into `data/`, independently of this job.

## Automating MarginNowV2 / FinancialSummary downloads from Jasper (optional)

There's a separate exe that downloads Jasper reports. One **keyword** is configured (inside the exe itself) to download several reports at once, each to its own specified output path — no username, just the keyword and then a **password**, one after another in its console window. `utils/jasper_downloader.py` automates this by piping both into the exe's stdin, so it runs unattended as part of every snapshot.

**One-time setup:**

1. In the exe's own configuration, set up (or reuse) a keyword whose reports include `MarginNowV2` and `FinancialSummary`, with output paths pointing at `data/MarginNowV2.xls` and `data/FinancialSummary.xls`.
2. Fill in `jasper_config.json` (copy `jasper_config.example.json` if you deleted it) with the exe's path and that keyword:
   ```json
   {
     "exe_path": "C:\\path\\to\\JasperDownloader.exe",
     "credential_username": "jasper",
     "keyword": "YOUR_KEYWORD",
     "reports": ["MarginNowV2", "FinancialSummary"]
   }
   ```
   `reports` here is just a note-to-self of what the keyword covers — it isn't sent to the exe. This file has no password in it, just the exe path and the (non-secret) keyword. `credential_username` is only a label for where the password is stored locally (see below); it's not sent to the exe either, since the exe itself takes no username.
3. Store the password once, so the scheduled job never needs a human to type it:
   ```bash
   python setup_jasper_password.py
   ```
   This prompts for the password with hidden input and stores it in **Windows Credential Manager** via the `keyring` package — it's never written to disk in plain text or logged.

Once both are in place, `run_snapshot()` (and therefore `archive_snapshot.py` and the "Run snapshot now" button) automatically runs the exe once with that keyword before archiving, downloading both reports in one go. If `jasper_config.json` is missing or still has placeholder values, this step is skipped with a log message and everything else keeps working exactly as before.

Confirmed working end-to-end (2026-09-18): the exe requires its working directory to be its own install folder (it looks for `../ConfigFiles/config.json` and `../sfx/*.mp3` relative to wherever it's launched from), so `jasper_downloader.py` launches it with `cwd` set to the exe's own folder. After downloading, it asks "Would you like to run another task? (ENTER/y to run again)" and then "Please press ENTER to exit." — both get answered automatically (decline, then confirm exit) so the exe closes on its own once it's done, in ~2-3 minutes. Without answering those, every run used to sit there until the timeout force-killed it and got logged as a failure, even though the login and both downloads had already succeeded — if `archive_log.txt` ever shows a run taking the full timeout again, check for a new/different post-download prompt first before assuming it's an auth problem. The timeout defaults to 400s as a safety ceiling — override it with an optional `"timeout_seconds"` key in `jasper_config.json` if needed.

### Hourly refresh (separate from the 7x/day archive schedule)

`refresh_jasper_data.py` just runs the Jasper download step on its own, independent of `archive_snapshot.py` — so `MarginNowV2.xls`/`FinancialSummary.xls` can refresh hourly while the position-ratio archive keeps its own 7x/day cadence tied to the Outlook emails. `archive_snapshot.py` always reads whichever files are newest in `data/` when it runs, so the two schedules don't need to line up.

Register the hourly task once from PowerShell:

```powershell
$pythonExe = "C:\Users\ruoqingyuan\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$workDir = "C:\Users\ruoqingyuan\Desktop\NBS dashboard"

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "refresh_jasper_data.py" -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-ScheduledTask -TaskName "NBS Jasper Hourly Refresh" -Action $action -Trigger $trigger -Description "Hourly download of MarginNowV2.xls and FinancialSummary.xls from Jasper"
```

It logs to both the console and `data/jasper_refresh_log.txt`.

## Remarks & supporting documents

On the Prop accounts page, anyone can log a free-text remark against a prop account and optionally attach a screenshot (PNG/JPG) — meant for documenting the reason behind a breach or a follow-up action. It's a running log per account (not tied to a specific archived snapshot), stored in the `remarks` table with screenshots saved under `data/attachments/`.

## Testing periods

Also on the Prop accounts page: set a start/end date (+ optional note and an approval-email upload as proof — `.png`/`.jpg`/`.pdf`/`.msg`/`.eml`) for an account when a breach is expected due to testing. While today falls within that range, the account shows "Testing until [date]" instead of an urgent flag. Once the end date passes and the account is *still* breaching, it's called out separately (a "Breaching past testing period" KPI plus a dedicated alert) — since that's no longer explained by testing. Periods can be edited or deleted from their own expander (editing/removing the proof cleans up the old file).

A breach covered by an *active* testing period isn't counted in the "Breaching accounts" KPI or the breach alert at all — it's excused for as long as testing is in progress.

## P&L alert thresholds

Also on the Prop accounts page: set a **default** loss floor per P&L metric (intraday, daily prev-day, monthly), then optionally **override it per account** — any account whose P&L for that metric falls at or below its *effective* threshold (its own override if set, else the default) triggers its own alert, plus an "Accounts past P&L threshold" KPI. Leave a field blank to disable alerting for that metric/account. Persisted to `data/pnl_alert_thresholds.json` (`utils/pnl_thresholds.py`), so it survives restarts; independent of the breach/testing-period logic above (a different thing being monitored).

## Alert log & shared alert panel

Every prop-account alert condition (breach, breach-past-testing, P&L threshold) is reconciled into a persistent `alert_log` table (`archive_db.sync_alerts`) instead of only being computed live on page load — so there's an actual history, not just current state. An alert gets one row when it first becomes true and stays that one row (no duplicates on every rerun) until the condition clears, at which point `resolved_at` is set.

`utils/alert_engine.py` holds the shared "build prop status + compute alerts" logic, called from three places so the log stays current regardless of who's looking at what:
- The Prop accounts page, on every load.
- `archive_snapshot.py` / `snapshot_runner.py`, on every scheduled run — so alerts stay fresh even when nobody has the dashboard open.
- `utils/alert_panel.py`, rate-limited to once per 30s (it re-reads `MarginNowV2.xls`, so it doesn't re-scan on every single click).

A hideable **Alerts** panel (`utils/alert_panel.py`) is mounted once in `streamlit_app.py`, above `page.run()`, so it shows on **every tab** — collapsed by default, with the active count in its label, expanding to show both currently-active alerts and resolved history. Only the Prop accounts page populates it today; any other page can contribute by tagging its own alerts with a distinct `source` and calling `archive_db.sync_alerts(source, alerts)`.

## Margin calls (REMOVED — page and loader deleted Oct 2026; backup zip kept beside project)

Search past margin call records across the monthly summary reports dropped into `data/margin_call_reports/` (any filename, `.xlsx`/`.xls`). Each report file has **one sheet per trading date** (sheet name like `"30 Jan 26"`), with a title block above the real header (row 4) and a legend block below the data. There's no per-row date column, so the record date is parsed from the **sheet name** instead.

Every account occupies a **fixed 6-row block**: `Client No`/`AE Code`/`Acct Type`/`Ccy` are literally repeated on all 6 rows, but the summary fields (`Margin Call`, `Equity(S$)`, `Margin Ratio(%)`, etc.) are only populated on the block's first row — the rest is blank there and only carries extra `Positions`/`Remark` lines, since **an account can have more than one remark** for the same margin call instance (e.g. a deficit note, then a follow-up, then "CALL MET"), logged across consecutive rows. `utils/margin_call_loader.py` groups contiguous rows sharing the same `Client No` into one block, takes the summary fields from wherever they're set, and combines all `Positions`/`Remark` values in row order into one string per record. A block with no non-null `Margin Call` anywhere in it is legend/footer text, not a real record, and is dropped. All report files in the folder are loaded and concatenated, cached by (filename, mtime) per file.

Search by account number (partial match), AE code, and date range; results are downloadable as CSV.

## Archiving

`archive_snapshot.py` (via `utils/snapshot_runner.py`, shared with the "Run snapshot now" button):
1. Fetches the latest position-ratio report from Outlook into `data/` (if not already downloaded).
2. Reads the current prop account state and appends one row per account to `data/prop_archive.db` (`prop_snapshots` table).
3. Reads `FinancialSummary.xls` and archives each prop account's `TotalEquity` under today's `Report_Date` into `eod_snapshots`, skipping dates already archived.

Run it once manually to test:

```bash
python archive_snapshot.py
```

It logs to both the console and `data/archive_log.txt`.

### Schedule

The source emails go out at **11:00, 13:00, 15:00, 19:00, 22:00, 02:00, and 05:00** daily — 7 runs a day, starting with the 11am batch and ending with the 5am batch. To avoid a run firing before the email has actually landed, the scheduled task runs **5 minutes after** each of those times: 11:05, 13:05, 15:05, 19:05, 22:05, 02:05, 05:05.

Register this once from PowerShell (adjust the python path/working directory if you move the project):

```powershell
$pythonExe = "C:\Users\ruoqingyuan\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$workDir = "C:\Users\ruoqingyuan\Desktop\NBS dashboard"
$times = "11:05", "13:05", "15:05", "19:05", "22:05", "02:05", "05:05"

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "archive_snapshot.py" -WorkingDirectory $workDir
$triggers = $times | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
Register-ScheduledTask -TaskName "NBS Prop Account Archive" -Action $action -Trigger $triggers -Description "Fetch prop account report from Outlook and archive P&L / position ratio"
```

To check it's registered or remove it later:

```powershell
Get-ScheduledTask -TaskName "NBS Prop Account Archive"
Unregister-ScheduledTask -TaskName "NBS Prop Account Archive" -Confirm:$false
```

## Settings & admin password

All configuration lives on the **Settings** tab. It is read-only until the admin password is entered. The password is not stored in the code: set the `NBS_ADMIN_PASSWORD` environment variable, or put `admin_password = "..."` in `.streamlit/secrets.toml` (gitignored), then restart the app. With neither set, nobody can modify settings.

**Credit excess:** `MarginNowV2.xls`'s live balance carries a static credit excess that `FinancialSummary.xls`'s EOD balance doesn't. The per-account amount saved on Settings is subtracted from the live balance before intraday/monthly P&L and alerts are computed (`alert_engine.build_prop_status`, also applied to archived batches on Archive history). Stored in the `credit_excess` table.

**Monthly adjustments:** removed from the month's first trading day -- from `intraday_pnl` until the first EOD of the month exists, from `daily_pnl_prev_day` once it is the last EOD, and from `monthly_pnl` throughout (matches the Excel monitor's `Update_MTD_History`).

## Files

```
streamlit_app.py            # entry point / navigation, mounts the shared alert panel
app_pages/
    prop_monitor.py           # tab 2: prop account P&L, position ratio, adjustments, testing periods, remarks
    archive_history.py          # tab 4: archive browser, per-batch download
utils/
    data_loader.py              # reads/cleans the .xls files (no Streamlit dependency)
    cached_loaders.py           # Streamlit cache_data wrappers, keyed on file mtime
    archive_db.py                # SQLite archive: prop snapshots, EOD history, remarks, adjustments, testing periods, alert_log
    alert_engine.py               # shared "build prop status + compute alerts" logic (no Streamlit dependency)
    alert_panel.py                 # hideable cross-tab alert panel, mounted in streamlit_app.py
    pnl_thresholds.py                # per-account P&L alert threshold config, persisted to data/pnl_alert_thresholds.json
    outlook_fetcher.py             # pulls the latest position-ratio report from Outlook
    jasper_downloader.py            # automates the Jasper exe (optional, see above)
    snapshot_runner.py               # shared routine: jasper + outlook + archive prop + archive EOD
archive_snapshot.py           # standalone scheduled job (see above)
setup_jasper_password.py       # one-time: store the Jasper password in Credential Manager
jasper_config.example.json      # template — copy to jasper_config.json and fill in
jasper_config.json               # your exe path + keywords (no password in it)
data/                             # all input/generated data files (see above)
```
