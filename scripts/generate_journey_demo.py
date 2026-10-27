"""Generate an explicitly synthetic UI fixture using the production classifier."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "gwm_journey_demo_logic", root / "custom_components" / "gwm_thailand_connect" / "journey.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Journey classifier is not available")
    logic = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = logic
    spec.loader.exec_module(logic)
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=25)
    observations = []
    for seconds in range(0, 1501, 60):
        if seconds <= 600:
            latitude, longitude, odometer = 13.74, 100.50, 12000.0
        elif seconds <= 780:
            fraction = (seconds - 600) / 180
            latitude, longitude, odometer = 13.74 + fraction * 0.004, 100.50 + fraction * 0.006, 12000 + fraction
        elif seconds <= 1200:
            latitude, longitude, odometer = 13.744, 100.506, 12001.0
        elif seconds <= 1380:
            fraction = (seconds - 1200) / 180
            latitude, longitude, odometer = 13.744 + fraction * 0.004, 100.506 + fraction * 0.006, 12001 + fraction
        else:
            latitude, longitude, odometer = 13.748, 100.512, 12002.0
        door_open = seconds in {120, 180, 900}
        vehicle_power_off = 60 <= seconds <= 600 or 840 <= seconds <= 1200
        locked = 240 <= seconds <= 600 or 1020 <= seconds <= 1200 or seconds >= 1440
        instant = start + timedelta(seconds=seconds)
        observation = logic.Observation.from_snapshot({
            "timestamps": {"acquisition_time": instant.isoformat()},
            "location": {"latitude": latitude, "longitude": longitude},
            "values": {
                **dict.fromkeys(logic.DOORS, False), "door_front_driver_open": door_open,
                "locked": locked, "soc": round(65 - seconds / 600, 1), "odometer_km": odometer,
                "vehicle_power_off": vehicle_power_off,
            },
        }, instant)
        observations.append(observation)
    summary = logic.summarize(observations, observations[-1].time)
    latest = observations[-1]
    fixture = {
        **summary, "status": "recording", "retention_days": 7, "sample_limit": logic.MAX_SAMPLES,
        "current": {
            "availability": "available", "position": latest.position,
            "reported_at": latest.time.isoformat(),
            "expires_at": (latest.time + timedelta(seconds=300)).isoformat(),
            "battery": latest.soc, "locked": latest.locked,
            "doors": dict(zip(logic.DOORS, latest.doors, strict=True)), "ac_active": False,
            "vehicle_power_off": latest.vehicle_power_off,
        },
    }
    destination = root / "docs" / "demo" / "journey-fixture.json"
    destination.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"Generated synthetic journey fixture: {len(summary['stops'])} stops, {len(summary['doors'])} door intervals")


if __name__ == "__main__":
    main()
