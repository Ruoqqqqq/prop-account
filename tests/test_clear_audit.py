import zipfile

import pandas as pd
import pytest

from utils import archive_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_db, "BASE_DIR", tmp_path)
    path = tmp_path / "t.db"
    conn = archive_db.get_connection(path)
    rows = [
        # id, triggered_at, resolved_at, client
        (1, "2026-01-05T10:00:00", "2026-01-05T11:00:00", "OLD_RESOLVED"),
        (2, "2026-01-06T10:00:00", None, "OLD_STILL_ACTIVE"),
        (3, "2026-09-01T10:00:00", "2026-09-01T11:00:00", "NEW_RESOLVED"),
    ]
    for i, t, r, c in rows:
        conn.execute(
            "INSERT INTO alert_log (id, triggered_at, resolved_at, source, alert_type, client_no, message) "
            "VALUES (?, ?, ?, 'prop_accounts', 'breach', ?, 'm')", (i, t, r, c))
    conn.execute("INSERT INTO alert_actions (alert_id, action, username, remark, created_at) VALUES (1, 'dismiss', 'Alice', 'ok', 'x')")
    conn.execute("INSERT INTO settings_audit (created_at, username, area, action) VALUES ('2026-01-10T09:00:00', 'Bob', 'Credit excess', 'old change')")
    conn.execute("INSERT INTO settings_audit (created_at, username, area, action) VALUES ('2026-09-10T09:00:00', 'Bob', 'Credit excess', 'new change')")
    conn.commit()
    conn.close()
    return path


def test_preview_counts_only_resolved_old_alerts(db):
    assert archive_db.count_clearable_audit("2026-06-01", db) == {"alerts": 1, "settings": 1}


def test_clear_exports_first_then_removes_and_logs(db, tmp_path):
    out = archive_db.clear_audit_history("2026-06-01", True, True, "Admin Al", db_path=db, archive_dir=tmp_path / "arch")
    assert out["alerts_removed"] == 1 and out["settings_rows_removed"] == 1

    remaining = archive_db.load_alerts_with_status(db)
    assert sorted(remaining["client_no"]) == ["NEW_RESOLVED", "OLD_STILL_ACTIVE"]   # active alert kept, recent kept
    assert list(archive_db.load_settings_audit(db)["action"]) == ["new change"]

    with zipfile.ZipFile(tmp_path / out["archive_file"]) as zf:
        assert set(zf.namelist()) == {"alert_log.csv", "alert_actions.csv", "settings_audit.csv"}
        assert "OLD_RESOLVED" in zf.read("alert_log.csv").decode()
        assert "Alice" in zf.read("alert_actions.csv").decode()

    log = archive_db.load_audit_clear_log(db).iloc[0]
    assert log["username"] == "Admin Al" and log["alerts_removed"] == 1 and log["cutoff"] == "2026-06-01"


def test_scope_can_be_limited(db, tmp_path):
    archive_db.clear_audit_history("2026-06-01", False, True, "Al", db_path=db, archive_dir=tmp_path / "arch")
    assert len(archive_db.load_alerts_with_status(db)) == 3        # alerts untouched
    assert len(archive_db.load_settings_audit(db)) == 1


def test_nothing_to_clear_still_logged_without_export(db, tmp_path):
    out = archive_db.clear_audit_history("2025-01-01", True, True, "Al", db_path=db, archive_dir=tmp_path / "arch")
    assert out == {"alerts_removed": 0, "settings_rows_removed": 0, "archive_file": None}
    assert len(archive_db.load_audit_clear_log(db)) == 1
