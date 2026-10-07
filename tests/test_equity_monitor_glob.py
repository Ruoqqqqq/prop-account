import os

from utils import data_loader


def test_picks_newest_file_whether_email_or_jasper_name(tmp_path):
    email = tmp_path / "Propriety_Account_Equity_Monitor_V2-202610070500.xls"
    jasper = tmp_path / "Propriety_Account_Equity_MonitorV2.xls"
    email.write_text("x")
    jasper.write_text("x")
    os.utime(email, (1_000, 1_000))
    os.utime(jasper, (2_000, 2_000))
    assert data_loader.latest_equity_monitor_path(tmp_path) == jasper
    os.utime(email, (3_000, 3_000))
    assert data_loader.latest_equity_monitor_path(tmp_path) == email
