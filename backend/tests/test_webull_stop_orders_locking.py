from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from webull_stop_orders import get_exit_orders, pop_exit_order_by_id, record_exit_order

TICKER = "AAPL"
BACKEND_DIR = Path(__file__).resolve().parents[1]

# The real threat model this locking exists for is multiple GUNICORN
# WORKER PROCESSES - genuinely separate OS processes with separate memory,
# not threads within one process. A thread-based test (the ThreadPoolExecutor
# version this replaces) only proves the lock serializes concurrent
# CALLERS within a single interpreter, which Python's own GIL already
# does a lot of incidental serializing for anyway (file I/O releases the
# GIL, but the margin for a false pass is real). Launching genuine child
# processes via subprocess.Popen - not multiprocessing.Process, whose
# default 'spawn' start method on macOS is fragile when the worker
# function lives in a pytest-collected test module rather than a real
# top-level package - is both more realistic AND more robust here.


def _record_worker_command(user_id: str, ticker: str, order_id: str, order_type: str) -> list[str]:
    script = (
        "import sys; "
        "from webull_stop_orders import record_exit_order; "
        "record_exit_order(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])"
    )
    return [sys.executable, "-c", script, user_id, ticker, order_id, order_type]


def _pop_worker_command(user_id: str, ticker: str, order_id: str) -> list[str]:
    script = (
        "import sys; "
        "from webull_stop_orders import pop_exit_order_by_id; "
        "pop_exit_order_by_id(sys.argv[1], sys.argv[2], sys.argv[3])"
    )
    return [sys.executable, "-c", script, user_id, ticker, order_id]


def _run_concurrently(commands: list[list[str]]) -> None:
    """Launches every command as a genuinely separate OS process (real
    PID, real independent memory/GIL) at once via Popen, then waits for
    all of them - true process-level concurrency, not thread-level."""
    env = dict(os.environ)  # PLUTO_DATA_DIR (set by conftest.py) and every other test-fixed env var
    processes = [subprocess.Popen(cmd, cwd=str(BACKEND_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for cmd in commands]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, f"worker process failed: {stderr.decode('utf-8', errors='replace')}"


def test_concurrent_record_exit_order_calls_across_real_processes_lose_no_updates(user_id):
    """Without a lock around the read-modify-write cycle, two SEPARATE
    PROCESSES can each read the same starting (empty) state, append their
    own order in memory, and write - the second write silently clobbers
    the first's addition even though _write itself is atomic (atomicity
    prevents a TORN file, not a LOST update between two racing full
    read-modify-write cycles). fcntl.flock is a real, OS-level,
    cross-process lock - this proves it actually serializes genuinely
    separate processes, matching gunicorn's real worker-process model,
    not just concurrent callers sharing one interpreter."""
    order_ids = [f"order-{i}" for i in range(20)]
    commands = [_record_worker_command(user_id, TICKER, order_id, "stop") for order_id in order_ids]
    _run_concurrently(commands)

    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert tracked_ids == set(order_ids)  # every concurrent process's write survived - none lost


def test_concurrent_pop_and_record_across_real_processes_lose_no_updates(user_id):
    """A more realistic mix - some processes adding new legs (as a resize
    would) while others remove different, unrelated legs (as a confirmed
    cancellation would) - every operation's effect must be reflected in
    the final state, with no lost update in either direction, across
    genuinely separate OS processes."""
    kept_ids = [f"keep-{i}" for i in range(10)]
    removed_ids = [f"remove-{i}" for i in range(10)]
    for order_id in removed_ids:
        record_exit_order(user_id, TICKER, order_id, "target")

    commands = [_record_worker_command(user_id, TICKER, order_id, "stop") for order_id in kept_ids]
    commands += [_pop_worker_command(user_id, TICKER, order_id) for order_id in removed_ids]
    _run_concurrently(commands)

    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert tracked_ids == set(kept_ids)  # all adds landed, all removes landed, nothing lost either way
