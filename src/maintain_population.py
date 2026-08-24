from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import calibrate_population as base
import calibrate_population_resilient as calibration

CACHE_PATH = Path("data/population_profiles.json")
MAINTENANCE_TARGET = int(os.getenv("GT7_MAINTENANCE_TARGET", "2200"))
WEEKLY_REPLACEMENTS = int(os.getenv("GT7_MAINTENANCE_REPLACE", "100"))
MAINTENANCE_SAMPLE = int(os.getenv("GT7_MAINTENANCE_SAMPLE", "600"))


def collected_sort_key(item):
    row = item[1]
    value = row.get("collected_at")
    if not isinstance(value, str):
        return "0000-00-00T00:00:00+00:00"
    return value


def prune_oldest_profiles() -> int:
    if not CACHE_PATH.exists():
        return 0

    payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    profiles = payload.get("profiles") or {}
    if not profiles:
        return 0

    # Remove a fixed, small fraction of the oldest observations each maintenance
    # cycle. New observations then refill the rolling reference population.
    remove_count = min(WEEKLY_REPLACEMENTS, len(profiles))
    oldest = sorted(profiles.items(), key=collected_sort_key)[:remove_count]
    for key, _ in oldest:
        profiles.pop(key, None)

    payload["profiles"] = profiles
    payload["maintenance_pruned_at"] = datetime.now(timezone.utc).isoformat()
    payload["maintenance_profiles_removed"] = remove_count
    payload["target_valid_profiles"] = MAINTENANCE_TARGET
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return remove_count


def main() -> None:
    removed = prune_oldest_profiles()
    print(f"Rolling population maintenance: removed {removed} oldest profiles")

    # Reuse the validated multi-event collector and the cross-DR weighting model,
    # but with a bounded rolling population instead of the one-time bootstrap goal.
    calibration.TARGET_VALID_REFERENCE = MAINTENANCE_TARGET
    calibration.EVENTS_PER_RUN = int(os.getenv("GT7_CALIBRATION_EVENTS_PER_RUN", "3"))
    base.TARGET_SAMPLE = MAINTENANCE_SAMPLE

    calibration.main()


if __name__ == "__main__":
    main()
