"""Lightweight machine & self-process memory telemetry using psutil."""

from __future__ import annotations

import os

import psutil

from jawis.storage.db import AllowedApp


class SystemTelemetry:
    """Provides low-overhead CPU, RAM, battery, self-RSS, and allowlisted app status snapshots."""

    def __init__(self) -> None:
        self._proc = psutil.Process(os.getpid())
        psutil.cpu_percent(interval=None)

    def self_rss_mb(self) -> float:
        """Return resident memory (MiB) of the current J.A.R.V.I.S. backend process."""
        return round(self._proc.memory_info().rss / (1024 * 1024), 1)

    def fast_snapshot(self) -> dict[str, object]:
        """Lightweight 2s WebSocket heartbeat avoiding expensive process_iter scans."""
        vm = psutil.virtual_memory()
        battery = psutil.sensors_battery()
        return {
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "cpu_cores": psutil.cpu_count(logical=True) or 1,
            "ram_used_gb": round(vm.used / (1024**3), 2),
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_percent": round(vm.percent, 1),
            "battery_percent": round(battery.percent, 1) if battery else None,
            "battery_plugged": bool(battery.power_plugged) if battery else None,
            "jawis_rss_mb": self.self_rss_mb(),
        }

    def snapshot(self, allowed_apps: list[AllowedApp]) -> dict[str, object]:
        """Return fast telemetry enriched with running status for each allowlisted app."""
        data = self.fast_snapshot()
        running_names: set[str] = set()
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name:
                    running_names.add(name)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        data["allowed_apps_status"] = [
            {
                "app_key": app.app_key,
                "display_name": app.display_name,
                "running": any(p.lower() in running_names for p in app.process_names),
            }
            for app in allowed_apps
        ]
        return data
