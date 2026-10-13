from __future__ import annotations

from flask import url_for

import app as pluto_app


def _unwrap(view):
    seen = set()
    while hasattr(view, "__wrapped__") and id(view) not in seen:
        seen.add(id(view))
        view = view.__wrapped__
    return view


def test_moved_routes_keep_endpoint_names_and_live_under_routes():
    rules = {(rule.rule, rule.endpoint) for rule in pluto_app.app.url_map.iter_rules()}
    assert ("/healthz", "healthz") in rules
    assert ("/login", "login_page") in rules
    assert ("/api/safety/close-everything", "api_close_everything") in rules
    assert ("/dashboard", "dashboard_page") in rules
    assert ("/mission-control", "dashboard_page") in rules
    assert ("/api/chart-levels/watchlist", "api_chart_levels_watchlist") in rules
    for name in ("auth", "admin", "pages", "market", "account", "trading"):
        assert name in pluto_app.app.blueprints

    healthz = _unwrap(pluto_app.app.view_functions["healthz"])
    close_everything = _unwrap(pluto_app.app.view_functions["api_close_everything"])
    assert "/routes/" in healthz.__code__.co_filename
    assert "/routes/" in close_everything.__code__.co_filename
    assert healthz.__globals__ is pluto_app.__dict__
    assert close_everything.__globals__ is pluto_app.__dict__

    with pluto_app.app.test_request_context():
        assert url_for("login_page") == "/login"
        assert url_for("dashboard_page") in ("/dashboard", "/mission-control")
        assert url_for("admin_page") == "/admin"
