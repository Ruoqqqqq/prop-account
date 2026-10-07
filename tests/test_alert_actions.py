import pandas as pd
import pytest

from utils import archive_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_db, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(archive_db, "BASE_DIR", tmp_path)
    path = tmp_path / "t.db"
    archive_db.sync_alerts("prop_accounts", [
        {"alert_type": "breach", "client_no": "A", "message": "A breaching"},
        {"alert_type": "pnl_intraday_pnl", "client_no": "B", "message": "B loss"},
    ], db_path=path)
    return path


def test_new_alerts_are_pending(db):
    pending = archive_db.load_pending_alerts(db)
    assert sorted(pending["client_no"]) == ["A", "B"]


def test_any_action_handles_the_alert(db):
    alert_id = int(archive_db.load_pending_alerts(db).query("client_no == 'A'")["id"].iloc[0])
    ok, other = archive_db.record_alert_action(alert_id, "dismiss", "Alice", "known, covered", db_path=db)
    assert ok and other is None
    assert list(archive_db.load_pending_alerts(db)["client_no"]) == ["B"]
    row = archive_db.load_alerts_with_status(db).query("client_no == 'A'").iloc[0]
    assert row["handled_by"] == "Alice" and row["action"] == "dismiss" and row["action_remark"] == "known, covered"


def test_second_user_is_told_a_teammate_handled_it(db):
    alert_id = int(archive_db.load_pending_alerts(db)["id"].iloc[0])
    assert archive_db.record_alert_action(alert_id, "dismiss", "Alice", "first", db_path=db) == (True, None)
    ok, handled_by = archive_db.record_alert_action(alert_id, "supporting", "Bob", "too late", db_path=db)
    assert (ok, handled_by) == (False, "Alice")
    conn = archive_db.get_connection(db)
    assert conn.execute("SELECT COUNT(*) FROM alert_actions WHERE alert_id = ?", (alert_id,)).fetchone()[0] == 1
    conn.close()


def test_file_upload_is_stored_with_the_action(db, tmp_path):
    alert_id = int(archive_db.load_pending_alerts(db)["id"].iloc[0])
    archive_db.record_alert_action(alert_id, "supporting", "Alice", "evidence", b"hello", "proof.txt", db_path=db)
    row = archive_db.load_alerts_with_status(db).query(f"id == {alert_id}").iloc[0]
    assert (tmp_path / row["action_file"]).read_bytes() == b"hello"


def test_unknown_alert_and_unknown_action(db):
    assert archive_db.record_alert_action(9999, "dismiss", "Alice", "x", db_path=db) == (False, None)
    with pytest.raises(ValueError):
        archive_db.record_alert_action(1, "ignore", "Alice", db_path=db)


def test_handled_alert_stays_handled_when_condition_persists_but_new_instance_is_open_again(db):
    a_id = int(archive_db.load_pending_alerts(db).query("client_no == 'A'")["id"].iloc[0])
    archive_db.record_alert_action(a_id, "dismiss", "Alice", "ok", db_path=db)
    # same condition still true -> no duplicate row, still handled
    archive_db.sync_alerts("prop_accounts", [
        {"alert_type": "breach", "client_no": "A", "message": "A breaching"},
        {"alert_type": "pnl_intraday_pnl", "client_no": "B", "message": "B loss"},
    ], db_path=db)
    assert list(archive_db.load_pending_alerts(db)["client_no"]) == ["B"]
    # condition clears, then returns -> a brand new alert needs action again
    archive_db.sync_alerts("prop_accounts", [{"alert_type": "pnl_intraday_pnl", "client_no": "B", "message": "B loss"}], db_path=db)
    archive_db.sync_alerts("prop_accounts", [
        {"alert_type": "breach", "client_no": "A", "message": "A breaching again"},
        {"alert_type": "pnl_intraday_pnl", "client_no": "B", "message": "B loss"},
    ], db_path=db)
    assert sorted(archive_db.load_pending_alerts(db)["client_no"]) == ["A", "B"]


def test_settings_audit_and_remark_username(tmp_path):
    db = tmp_path / "t.db"
    archive_db.log_settings_change("Alice", "Credit excess", "Saved credit excess", "A: 100.00", db_path=db)
    audit = archive_db.load_settings_audit(db)
    assert audit.iloc[0][["username", "area", "detail"]].tolist() == ["Alice", "Credit excess", "A: 100.00"]
    archive_db.add_remark("A", "note", None, None, db_path=db, username="Bob")
    assert archive_db.load_remarks(db_path=db).iloc[0]["username"] == "Bob"
