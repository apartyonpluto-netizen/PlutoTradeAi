from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import alerts


# --- _locked: exclusive read-modify-write lock ------------------------------


def test_locked_blocks_a_second_acquirer_until_the_first_releases(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text("[]", encoding="utf-8")
    order = []

    def hold_then_release():
        with alerts._locked(path):
            order.append("first-acquired")
            time.sleep(0.3)
            order.append("first-released")

    holder = threading.Thread(target=hold_then_release)
    holder.start()
    time.sleep(0.05)  # give the holder time to acquire first
    with alerts._locked(path):
        order.append("second-acquired")
    holder.join()

    assert order == ["first-acquired", "first-released", "second-acquired"]


def test_locked_uses_a_sidecar_file_not_the_data_file_itself(tmp_path):
    path = tmp_path / "alerts.json"
    with alerts._locked(path):
        pass
    assert (tmp_path / "alerts.json.lock").exists()
    assert not path.exists()  # _locked itself never creates/touches the data file


# --- _save_json: atomic write -----------------------------------------------


def test_save_json_leaves_no_temp_files_behind(tmp_path):
    path = tmp_path / "data.json"
    alerts._save_json(path, [{"a": 1}])
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_text(encoding="utf-8").strip().startswith("[")


def test_save_json_replaces_atomically_not_in_place(tmp_path, monkeypatch):
    # Proves the write goes through a temp-file-then-rename, not path.write_text()
    # directly - patch os.replace to confirm it's actually called with a
    # temp path distinct from the destination.
    path = tmp_path / "data.json"
    calls = []
    real_replace = alerts.os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(alerts.os, "replace", spy_replace)
    alerts._save_json(path, [{"a": 1}])
    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == str(path)
    assert src != dst
    assert ".tmp-" in src


# --- add_manual_alert: concurrent writers don't clobber each other ---------


def test_concurrent_add_manual_alert_calls_do_not_lose_writes(user_id):
    # Simulates gunicorn's multi-worker deployment: many callers racing to
    # append DIFFERENT alerts for the SAME user. Without the lock around the
    # read-modify-write cycle, a later writer's read (of the pre-append
    # state) can silently clobber an earlier writer's already-saved append -
    # every one of these must still be present at the end.
    count = 25

    def _add(i: int):
        alerts.add_manual_alert(user_id, {"type": "manual", "ticker": "AAPL", "message": f"concurrent alert #{i}"})

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(_add, range(count)))

    stored = alerts.load_manual_alerts(user_id)
    messages = {item["message"] for item in stored}
    assert messages == {f"concurrent alert #{i}" for i in range(count)}
    assert len(stored) == count


def test_concurrent_mark_alert_read_calls_do_not_lose_writes(user_id):
    alert_ids = [f"alert-{i}" for i in range(25)]

    def _mark(alert_id: str):
        alerts.mark_alert_read(user_id, alert_id)

    with ThreadPoolExecutor(max_workers=len(alert_ids)) as pool:
        list(pool.map(_mark, alert_ids))

    read_ids = alerts._read_ids(user_id)
    assert read_ids == set(alert_ids)
