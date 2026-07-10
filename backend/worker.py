from __future__ import annotations

import os
import time

from backend.services.mission_refresh_worker import MissionRefreshWorker


def main() -> None:
    interval_seconds = int(os.getenv("PLUTO_REFRESH_INTERVAL_SECONDS", "600"))
    worker = MissionRefreshWorker(interval_seconds=interval_seconds)

    try:
        while True:
            worker.refresh_once()
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    main()
