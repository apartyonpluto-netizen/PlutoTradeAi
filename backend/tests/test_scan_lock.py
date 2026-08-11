from __future__ import annotations

import pytest

from scan_lock import ScanAlreadyRunningError, user_scan_lock


def test_lock_can_be_acquired_and_released_sequentially(user_id):
    with user_scan_lock(user_id):
        pass
    with user_scan_lock(user_id):
        pass


def test_overlapping_acquire_for_same_user_raises(user_id):
    with user_scan_lock(user_id):
        with pytest.raises(ScanAlreadyRunningError):
            with user_scan_lock(user_id):
                pass


def test_lock_is_released_after_an_exception_inside_the_block(user_id):
    with pytest.raises(ValueError):
        with user_scan_lock(user_id):
            raise ValueError("simulated scan failure")
    # The lock must not still be held after the exception propagated - a
    # crashed scan must not permanently wedge every future tick for this user.
    with user_scan_lock(user_id):
        pass


def test_different_users_do_not_contend(user_id, other_user_id):
    with user_scan_lock(user_id):
        with user_scan_lock(other_user_id):
            pass


def test_scan_already_running_error_is_a_friendly_api_error(user_id):
    with user_scan_lock(user_id):
        try:
            with user_scan_lock(user_id):
                pass
        except ScanAlreadyRunningError as error:
            assert error.status_code == 409
            assert error.error_code == "scan_already_running"
