from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from backend.brains.registry import BrainRegistry
from backend.scanner.registry import ScannerRegistry
from backend.services.market_data_service import get_stock_snapshot
from backend.services.mission_assignment_service import (
    load_all_user_stores,
    refresh_mission_profile,
    save_user_store,
)


class MissionRefreshWorker:
    def __init__(self, interval_seconds: int = 600) -> None:
        self.interval_seconds = max(60, interval_seconds)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._scanner_registry = ScannerRegistry()
        self._brain_registry = BrainRegistry()

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def refresh_once(self) -> None:
        stores = load_all_user_stores()
        for namespace, store in stores.items():
            missions = store.get("missions", [])
            updated_missions = []
            changed = False
            for mission in missions:
                ticker = mission.get("ticker")
                if not ticker:
                    updated_missions.append(mission)
                    continue

                snapshot = get_stock_snapshot(ticker)
                if not snapshot.get("success"):
                    updated_missions.append(mission)
                    continue

                overview = self._brain_registry.run_overview(ticker)
                scanner_overview = self._scanner_registry.run("overview")
                refreshed = refresh_mission_profile(mission, snapshot.get("data", {}), overview, scanner_overview)
                updated_missions.append(refreshed)
                changed = True

            if changed:
                store["missions"] = updated_missions
                save_user_store(namespace, store)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.refresh_once()
            self._stop_event.wait(self.interval_seconds)
