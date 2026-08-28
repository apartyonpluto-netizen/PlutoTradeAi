from __future__ import annotations

import re
from pathlib import Path

"""Found live 2026-08-28: a Preview Scan (20-40s of blocking work in a
single request - market scan plus per-ticker intelligence) could tie up an
entire gunicorn SYNC worker process for that whole span. With only 4 sync
workers total and no free thread within a busy process to fall back on,
Render's own /healthz check had nowhere to land if the other workers
happened to be busy too - confirmed live via Render's Events/logs: a real
"Instance failed: HTTP health check failed" + restart landed seconds after
a Preview Scan run, on the already-fixed code (see
test_account_hub_webull_sync_outside_lock.py for that separate, earlier
fix - this is a structurally different problem: request DURATION under
concurrent load, not a lock held across I/O).

Fix: --worker-class gthread --threads 3, so each of the 4 WORKER PROCESSES
gets its own small pool of THREADS - a slow scan occupying one thread no
longer blocks /healthz (or any other quick request) from being served by
a sibling thread in the same process. These tests prove the actual
deployed start commands request gthread with a real thread count, not
just that the line exists - a plain "was --workers 4 present" check would
have passed against the OLD, buggy sync configuration just as easily."""

REPO_ROOT = Path(__file__).resolve().parents[2]


def _start_commands() -> dict[str, str]:
    procfile_text = (REPO_ROOT / "Procfile").read_text(encoding="utf-8")
    procfile_command = next(
        line.split(":", 1)[1].strip() for line in procfile_text.splitlines() if line.startswith("web:")
    )

    render_yaml_text = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")
    render_yaml_match = re.search(r"^\s*startCommand:\s*(.+)$", render_yaml_text, re.MULTILINE)
    assert render_yaml_match, "render.yaml has no startCommand for the web service"

    return {"Procfile": procfile_command, "render.yaml": render_yaml_match.group(1).strip()}


def test_web_service_start_commands_use_threaded_gunicorn_workers():
    for source, command in _start_commands().items():
        assert "gunicorn" in command, f"{source}: expected a gunicorn start command, got {command!r}"
        assert "--worker-class gthread" in command, (
            f"{source}: expected --worker-class gthread (not the default sync) so a slow request "
            f"can't starve /healthz out of every worker process - got {command!r}"
        )

        threads_match = re.search(r"--threads[= ](\d+)", command)
        assert threads_match, f"{source}: --worker-class gthread with no explicit --threads count - got {command!r}"
        thread_count = int(threads_match.group(1))
        assert 2 <= thread_count <= 6, (
            f"{source}: --threads={thread_count} is outside the sane range - too low (1) reintroduces the "
            f"starvation bug, too high risks the concurrent-scan memory blowup from the 2026-08-25 OOM "
            f"incident on this same 2GB plan - got {command!r}"
        )


def test_procfile_and_render_yaml_start_commands_match():
    commands = _start_commands()
    assert commands["Procfile"] == commands["render.yaml"], (
        "Procfile and render.yaml's startCommand have drifted apart - "
        f"Procfile={commands['Procfile']!r} render.yaml={commands['render.yaml']!r}"
    )
