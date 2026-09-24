from __future__ import annotations

import os
import time

from backend.app import create_app
from backend.services.mission_refresh_worker import MissionRefreshWorker
from backend.services.paper_engine import OrderMonitorWorker


def main() -> None:
    app = create_app()
    paper_engine = app.extensions.get("paper_engine")
    interval_seconds = int(os.getenv("PLUTO_REFRESH_INTERVAL_SECONDS", "600"))
    worker = MissionRefreshWorker(interval_seconds=interval_seconds)
    order_monitor = None

    if paper_engine is not None and os.getenv("PLUTO_ENABLE_ORDER_MONITOR", "true").lower() in {"1", "true", "yes", "on"}:
        order_monitor = OrderMonitorWorker(
            engine=paper_engine,
            interval_seconds=int(os.getenv("PLUTO_ORDER_MONITOR_INTERVAL_SECONDS", "45")),
        )
        order_monitor.start()

    try:
        while True:
            worker.refresh_once()
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        worker.stop()
        if order_monitor:
            order_monitor.stop()


if __name__ == "__main__":
    main()
