from __future__ import annotations

import uuid

import auth
import app as pluto_app
from autonomy.research_log import record_research_decision
from calibration_store import write_calibration

"""/agent-map/3d and /api/agent-map: the same real data as /agent-map, exposed as JSON for a live
3D scene. Reporting only - the JSON is derived from the account's own recorded history."""


def _client_for(prefix: str):
    user = auth.register_user(f"{prefix}-{uuid.uuid4().hex[:10]}", "TestPassword123!")
    auth.approve_user(user["id"])
    client = pluto_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
    return client, user["id"]


def test_api_agent_map_reports_idle_with_no_history(user_id):
    write_calibration({"status": "never_run", "generated_at": "", "strategy_stats": {}})
    client, _ = _client_for("map3d-empty")
    response = client.get("/api/agent-map")
    body = response.get_json()
    assert response.status_code == 200 and body["success"] is True
    data = body["data"]
    assert data["scan_is_active"] is False
    assert data["feed"] == []
    assert len(data["nodes"]) == 12
    assert not any(node["active"] for node in data["nodes"])  # nothing may pulse without evidence


def test_api_agent_map_activity_follows_real_records_and_shadow_nodes_never_pulse(user_id):
    write_calibration({"status": "never_run", "generated_at": "", "strategy_stats": {}})
    client, uid = _client_for("map3d-live")
    record_research_decision(uid, {"ticker": "NVDA", "decision": "placed", "quantity": 2, "signal_snapshot": {"market_context": {"regime": "calm"}}})
    data = client.get("/api/agent-map").get_json()["data"]
    by_id = {node["id"]: node for node in data["nodes"]}
    assert data["scan_is_active"] is True
    assert by_id["strategy"]["active"] and by_id["scanner"]["active"] and by_id["options"]["active"]
    assert all(not by_id[name]["active"] for name in ("regime", "candlepattern", "neural", "optionsresearch", "tradingview", "outcomes"))
    assert data["feed"][0]["ticker"] == "NVDA" and data["feed"][0]["is_placed"] is True
    assert data["memory_feed_populated"] == 1 and data["memory_feed_total"] == 1


def test_api_agent_map_is_scoped_to_the_signed_in_account(user_id):
    write_calibration({"status": "never_run", "generated_at": "", "strategy_stats": {}})
    owner_client, owner_id = _client_for("map3d-owner")
    other_client, _ = _client_for("map3d-other")
    record_research_decision(owner_id, {"ticker": "TSLA", "decision": "skipped", "skip_category": "confidence_threshold", "reason_skipped": "confidence 20 below 55 threshold"})
    assert owner_client.get("/api/agent-map").get_json()["data"]["feed"][0]["ticker"] == "TSLA"
    assert other_client.get("/api/agent-map").get_json()["data"]["feed"] == []


def test_api_agent_map_requires_sign_in(user_id):
    anonymous = pluto_app.app.test_client()
    response = anonymous.get("/api/agent-map", follow_redirects=False)
    assert response.status_code in (401, 302)


def test_3d_page_renders_and_loads_only_local_scripts(user_id):
    write_calibration({"status": "never_run", "generated_at": "", "strategy_stats": {}})
    client, _ = _client_for("map3d-page")
    response = client.get("/agent-map/3d")
    body = response.data.decode("utf-8")
    assert response.status_code == 200
    assert "Agent Map · 3D" in body and 'id="am3dCanvas"' in body
    assert "/static/js/vendor/three.module.min.js" in body and "agent_map_3d.js" in body
    assert "cdn." not in body.split("</head>")[0].split("importmap")[1]  # the 3D library is vendored, not fetched from a CDN
    assert client.get("/static/js/vendor/three.module.min.js").status_code == 200
    assert client.get("/static/js/agent_map_3d.js").status_code == 200
    assert client.get("/agent-map").data.decode("utf-8").count("/agent-map/3d") >= 1  # linked from the 2D page
