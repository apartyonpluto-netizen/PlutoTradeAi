"""Data snapshots: contents, rotation, verification, and admin-only access.
Uses a temp data directory - never the real one."""

from __future__ import annotations

import json
import tarfile
from unittest.mock import patch

import pytest

import app as pluto_app
import auth
import backups


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "DATA_DIR", tmp_path)
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "users" / "u1").mkdir(parents=True)
    (tmp_path / "users" / "u1" / "overnight_orders.json").write_text(json.dumps([{"ticker": "AAPL"}]))
    (tmp_path / "users" / "u1" / "overnight_orders.json.lock").write_text("")
    (tmp_path / "users.json").write_text("{}")
    (tmp_path / "pre-restore-20260101T000000Z").mkdir()
    (tmp_path / "pre-restore-20260101T000000Z" / "old.json").write_text("{}")
    return tmp_path


def test_a_snapshot_contains_the_records_and_skips_locks_and_old_backups(data_dir):
    first = backups.create("test")
    assert first["created"] and first["files"] == 2
    with tarfile.open(data_dir / "backups" / first["name"]) as archive:
        names = set(archive.getnames())
    assert names == {"users/u1/overnight_orders.json", "users.json"}
    assert backups.verify(first["name"])["ok"]


def test_rotation_keeps_the_newest(data_dir, monkeypatch):
    monkeypatch.setattr(backups, "KEEP", 2)
    stamps = iter([s for s in ("20260101T000001Z", "20260101T000002Z", "20260101T000003Z") for _ in range(2)])

    class Clock:
        @staticmethod
        def now(tz=None):
            from datetime import datetime, timezone
            return datetime.strptime(next(stamps), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)

    with patch.object(backups, "_now", lambda: Clock.now()):
        for _ in range(3):
            backups.create()
    names = [b["name"] for b in backups.list_backups()]
    assert names == ["pluto-data-20260101T000003Z.tar.gz", "pluto-data-20260101T000002Z.tar.gz"]


def test_a_tampered_backup_fails_verification(data_dir):
    made = backups.create()
    path = data_dir / "backups" / made["name"]
    path.write_bytes(path.read_bytes() + b"x")
    assert not backups.verify(made["name"])["ok"]


def test_names_outside_the_backup_folder_are_refused(data_dir):
    backups.create()
    assert backups.path_for("../users.json") is None
    assert backups.path_for("users.json") is None


def test_a_low_disk_skips_the_backup(data_dir):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    with patch.object(backups.shutil, "disk_usage", return_value=usage(1, 1, 10)):
        assert backups.create()["created"] is False


def test_backup_routes_are_admin_only(user_id, data_dir):
    user = auth.register_user(f"bk-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        with patch.object(pluto_app, "is_admin", return_value=False):
            assert client.post("/api/admin/backups").status_code == 403
            assert client.get("/api/admin/backups/x/download").status_code == 403
        with patch.object(pluto_app, "is_admin", return_value=True):
            made = client.post("/api/admin/backups").get_json()["data"]["created"]
            assert made["created"]
            download = client.get(f"/api/admin/backups/{made['name']}/download")
            assert download.status_code == 200 and download.data[:2] == b"\x1f\x8b"
            assert client.get("/api/admin/backups/nope.tar.gz/download").status_code == 404
