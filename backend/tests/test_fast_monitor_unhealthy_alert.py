from __future__ import annotations

import os
from unittest.mock import patch

import app as pluto_app
from alerts import load_manual_alerts


def test_no_alert_fires_while_the_monitor_is_healthy(user_id):
    with patch.object(pluto_app, "_fast_monitor_health_status", return_value={"healthy": True, "reason": ""}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "is_admin", return_value=True):
        pluto_app._alert_admins_fast_monitor_unhealthy_if_needed()
    assert load_manual_alerts(user_id) == []


def test_admins_are_alerted_when_the_monitor_is_unhealthy(user_id, other_user_id):
    """other_user_id stands in for a non-admin account - it must NOT
    receive this alert, since the fast monitor's heartbeat is a system-wide
    operational concern for admins, not a per-account trading signal."""
    def _is_admin(uid: str) -> bool:
        return uid == user_id

    with patch.object(
        pluto_app, "_fast_monitor_health_status",
        return_value={"healthy": False, "reason": "the fast monitor has never run - its scheduler may not be configured"},
    ), patch.object(pluto_app, "list_all_user_ids", return_value=[user_id, other_user_id]), \
         patch.object(pluto_app, "is_admin", side_effect=_is_admin):
        pluto_app._alert_admins_fast_monitor_unhealthy_if_needed()

    admin_alerts = load_manual_alerts(user_id)
    assert len(admin_alerts) == 1
    assert admin_alerts[0]["type"] == "fast_monitor_unhealthy"
    assert load_manual_alerts(other_user_id) == []


def test_repeated_unhealthy_ticks_do_not_spam_duplicate_alerts(user_id):
    """Mirrors add_manual_alert's own content-hash dedup - this is what
    makes the alert "one-shot" per distinct condition, not something that
    needs a separate already-alerted flag: calling this repeatedly while
    still unhealthy must land exactly one alert record, not one per tick."""
    with patch.object(
        pluto_app, "_fast_monitor_health_status",
        return_value={"healthy": False, "reason": "no completed fast-monitor run in over 20 minutes"},
    ), patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "is_admin", return_value=True):
        pluto_app._alert_admins_fast_monitor_unhealthy_if_needed()
        pluto_app._alert_admins_fast_monitor_unhealthy_if_needed()
        pluto_app._alert_admins_fast_monitor_unhealthy_if_needed()

    assert len(load_manual_alerts(user_id)) == 1


def test_cron_trigger_endpoint_checks_fast_monitor_health_once_per_tick(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "_alert_admins_fast_monitor_unhealthy_if_needed") as mock_alert_check:
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/cron-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )

    assert response.status_code == 200
    mock_alert_check.assert_called_once()
