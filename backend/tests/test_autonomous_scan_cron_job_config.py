from __future__ import annotations

from pathlib import Path

"""Found live 2026-08-28: GitHub Actions' scheduled-workflow mechanism
proved unreliable for the every-5-minutes cadence the autonomous scan
needs - real run history showed runs landing hours apart instead of every
5 minutes, then stopping entirely for 22+ hours (confirmed NOT a
billing/quota issue - GitHub's own Actions minutes usage showed 0/2000
consumed). Fixed by adding a Render Cron Job (plutotradeai-autonomous-
scan-trigger) as the PRIMARY trigger - Render's own first-party scheduled-
invocation mechanism, on the same infrastructure as the web service
itself, exactly what api_autonomy_cron_trigger's own docstring in app.py
already anticipated. This test locks in that render.yaml actually
declares this service correctly - not just that render.yaml parses, but
that the specific service exists with a real schedule and the right
startCommand, so a future edit can't silently drop or misconfigure it
without a test failing."""

REPO_ROOT = Path(__file__).resolve().parents[2]


def _render_yaml_text() -> str:
    return (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")


def test_autonomous_scan_trigger_cron_job_is_declared():
    text = _render_yaml_text()
    assert "plutotradeai-autonomous-scan-trigger" in text, (
        "the Render Cron Job replacing the unreliable GitHub Actions scheduler is missing from render.yaml"
    )


def test_autonomous_scan_trigger_is_a_real_cron_job_type():
    text = _render_yaml_text()
    # Find the block for this specific service and confirm it's declared
    # as type: cron, not accidentally left as (or reverted to) a plain
    # worker/web service - a cron job is what makes Render's own scheduler
    # responsible for "every 5 minutes," not a sleep loop this app would
    # need to babysit itself.
    start = text.index("plutotradeai-autonomous-scan-trigger")
    block = text[max(0, start - 200) : start + 800]
    assert "type: cron" in block, f"expected 'type: cron' near the service declaration, got:\n{block}"


def test_autonomous_scan_trigger_has_a_five_minute_schedule_during_market_hours():
    text = _render_yaml_text()
    start = text.index("plutotradeai-autonomous-scan-trigger")
    block = text[start : start + 800]
    assert 'schedule: "*/5 13-21 * * 1-5"' in block, (
        f"expected the same 5-minute market-hours schedule as the GitHub Actions workflow it replaces, got:\n{block}"
    )


def test_autonomous_scan_trigger_runs_the_trigger_script():
    text = _render_yaml_text()
    start = text.index("plutotradeai-autonomous-scan-trigger")
    block = text[start : start + 800]
    assert "startCommand: python backend/trigger_autonomous_scan.py" in block, (
        f"expected the cron job to run trigger_autonomous_scan.py, got:\n{block}"
    )


def test_autonomous_scan_trigger_has_the_url_and_secret_it_needs():
    text = _render_yaml_text()
    start = text.index("plutotradeai-autonomous-scan-trigger")
    block = text[start : start + 800]
    assert "CRON_TRIGGER_URL" in block
    assert "/api/autonomy/cron-trigger" in block
    assert "CRON_SECRET" in block
    assert "sync: false" in block, "CRON_SECRET must be manually copied from the web service, not auto-generated fresh"


def test_github_actions_scheduler_workflow_still_exists_as_a_fallback():
    # Deliberately NOT deleted - same defense-in-depth precedent already
    # established for fast-monitor-scheduler.yml (kept as an independent
    # fallback layer, not a replacement). Overlapping triggers from both
    # this and the new Render Cron Job are safe - see
    # trigger_autonomous_scan.py's own module docstring.
    workflow_path = REPO_ROOT / ".github" / "workflows" / "autonomous-scan-scheduler.yml"
    assert workflow_path.exists(), "the GitHub Actions fallback should stay in place, not be deleted"
