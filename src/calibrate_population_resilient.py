from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import calibrate_population as base

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_EVENT_PAGES = 3
MAX_EVENT_ATTEMPTS = 3
EVENTS_PER_RUN = int(os.getenv("GT7_CALIBRATION_EVENTS_PER_RUN", "3"))
TARGET_VALID_REFERENCE = int(os.getenv("GT7_CALIBRATION_TARGET_VALID", "2000"))
MIN_VALID_TO_PUBLISH = int(os.getenv("GT7_CALIBRATION_MIN_VALID", "100"))
CACHE_PATH = Path("data/population_profiles.json")


def candidate_events(session: requests.Session) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for page in range(1, MAX_EVENT_PAGES + 1):
        url = base.GTSH_DAILY if page == 1 else f"{base.GTSH_DAILY}?page={page}&q="
        r = session.get(url, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        scored: list[tuple[int, str]] = []
        for link in soup.select('a[href*="/daily/leaderboard?event="], a[href*="/daily/leaderboard/?event="]'):
            href = link.get("href")
            full = urljoin(base.GTSH_DAILY, href) if href else None
            if not full or full in seen:
                continue
            text = link.parent.get_text(" ", strip=True) if link.parent else ""
            priority = 0 if "Daily Race C" in text else 1 if "Daily Race B" in text else 2
            scored.append((priority, full))
            seen.add(full)
        urls.extend(full for _, full in sorted(scored))
    return urls


def try_sample_event(session: requests.Session, event_url: str, target: int):
    last = None
    for attempt in range(1, MAX_EVENT_ATTEMPTS + 1):
        try:
            print(f"Trying leaderboard attempt {attempt}: {event_url}")
            return base.systematic_psns(session, event_url, target)
        except requests.HTTPError as exc:
            last = exc
            status = exc.response.status_code if exc.response is not None else None
            if status not in RETRY_STATUS:
                break
            wait = 2 ** attempt
            print(f"Transient leaderboard HTTP {status}; retrying in {wait}s")
            time.sleep(wait)
        except Exception as exc:
            last = exc
            break
    raise RuntimeError(f"Leaderboard unusable: {event_url}: {last}")


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {"profiles": {}, "unavailable": {}, "filtered": {}}
    try:
        obj = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("cache root is not an object")
        obj.setdefault("profiles", {})
        obj.setdefault("unavailable", {})
        obj.setdefault("filtered", {})
        return obj
    except Exception as exc:
        print(f"Population cache unreadable; starting fresh: {exc}")
        return {"profiles": {}, "unavailable": {}, "filtered": {}}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "target_valid_profiles": TARGET_VALID_REFERENCE,
        "profiles": cache["profiles"],
        "unavailable": cache["unavailable"],
        "filtered": cache["filtered"],
    }
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(psn: str) -> str:
    return psn.casefold()


def record_status(bucket: dict, psn: str, reason: str | None = None) -> None:
    bucket[norm(psn)] = {
        "psn_id": psn,
        "reason": reason,
        "last_attempt_at": datetime.now(timezone.utc).isoformat(),
    }


def profile_with_retry(session: requests.Session, psn: str):
    # Retry only transport/server failures. A structurally missing payload is treated
    # as unavailable, because many leaderboard PSNs are not tracked by the profile service.
    last_exc = None
    for attempt in range(1, 4):
        try:
            return base.fetch_profile_diagnostic(session, psn)
        except requests.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else None
            if status not in RETRY_STATUS:
                raise
            wait = 2 ** attempt
            print(f"Profile {psn}: HTTP {status}; retrying in {wait}s")
            time.sleep(wait)
        except requests.RequestException as exc:
            last_exc = exc
            wait = 2 ** attempt
            print(f"Profile {psn}: request error; retrying in {wait}s")
            time.sleep(wait)
    if last_exc:
        raise last_exc
    return None, ["unknown_profile_failure"]


def sample_summary(sample: list[dict]) -> dict:
    return {
        key: {
            "min": min(r[key] for r in sample),
            "median": sorted(r[key] for r in sample)[len(sample) // 2],
            "max": max(r[key] for r in sample),
        }
        for key in ("average_grid", "average_finish", "positions_gained_avg", "win_rate", "top5_rate")
    }


def main() -> None:
    session = requests.Session()
    session.headers.update(base.HEADERS)
    cache = load_cache()
    existing = len(cache["profiles"])
    print(f"Cumulative reference before run: {existing}/{TARGET_VALID_REFERENCE} valid profiles")

    # Once the target exists, recalculation is cheap and no longer needs to hit GTSH
    # until a future refresh policy is introduced.
    if existing >= TARGET_VALID_REFERENCE:
        sample = list(cache["profiles"].values())
        scores = base.metric_percentiles(base.user_daily(), sample)
        output = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "method": "cumulative_multi_event_validated_reference",
            "reference_population": "active/recent Daily Race leaderboard participants with at least 20 career Daily Races and physically valid career metrics",
            "target_valid_profiles": TARGET_VALID_REFERENCE,
            "valid_profiles": len(sample),
            "target_reached": True,
            "user_percentiles": scores,
            "sample_summary": sample_summary(sample),
        }
        base.OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Target already reached; calibration recomputed from cache only.")
        return

    events = candidate_events(session)
    if not events:
        raise RuntimeError("No candidate Daily Race leaderboards found")

    selected_events: list[dict] = []
    candidate_psns: list[str] = []
    candidate_seen: set[str] = set()
    # TARGET_SAMPLE is now the total number of leaderboard candidates attempted per run,
    # divided across several recent events rather than taken from one event only.
    per_event = max(100, math.ceil(base.TARGET_SAMPLE / max(1, EVENTS_PER_RUN)))
    leaderboard_failures: list[str] = []

    for event_url in events:
        if len(selected_events) >= EVENTS_PER_RUN:
            break
        try:
            psns, total = try_sample_event(session, event_url, per_event)
            selected_events.append({"url": event_url, "participants": total, "selected_psns": len(psns)})
            for psn in psns:
                key = norm(psn)
                if key in candidate_seen or key in cache["profiles"]:
                    continue
                candidate_seen.add(key)
                candidate_psns.append(psn)
        except Exception as exc:
            leaderboard_failures.append(str(exc))
            print(str(exc))

    if not selected_events:
        raise RuntimeError("No accessible leaderboard found")

    # Respect the run budget after cross-event de-duplication.
    candidate_psns = candidate_psns[: base.TARGET_SAMPLE]
    print(f"Events used: {len(selected_events)}; unique uncached PSNs this run: {len(candidate_psns)}")

    run_valid = 0
    unavailable = 0
    request_failures = 0
    rejection_counts = Counter()
    rejection_examples: dict[str, list[str]] = {}

    for i, psn in enumerate(candidate_psns, 1):
        try:
            row, reasons = profile_with_retry(session, psn)
            if row:
                row["collected_at"] = datetime.now(timezone.utc).isoformat()
                cache["profiles"][norm(psn)] = row
                cache["unavailable"].pop(norm(psn), None)
                cache["filtered"].pop(norm(psn), None)
                run_valid += 1
            else:
                reasons = reasons or ["unknown"]
                # Missing payload/key/stats means unavailable to the profile service,
                # not a failed statistical validation.
                if any(r in {"missing_encrypted_payload", "missing_decryption_key", "missing_daily_stats"} for r in reasons):
                    unavailable += 1
                    record_status(cache["unavailable"], psn, ",".join(reasons))
                else:
                    for reason in reasons:
                        rejection_counts[reason] += 1
                        rejection_examples.setdefault(reason, [])
                        if len(rejection_examples[reason]) < 5:
                            rejection_examples[reason].append(psn)
                    record_status(cache["filtered"], psn, ",".join(reasons))
        except Exception as exc:
            request_failures += 1
            print(f"Profile failed {psn}: {exc}")
        if i % 50 == 0 or i == len(candidate_psns):
            print(
                f"Profiles {i}/{len(candidate_psns)}; new valid {run_valid}; "
                f"unavailable {unavailable}; quality-filtered {sum(rejection_counts.values())}; "
                f"request failures {request_failures}"
            )
        time.sleep(base.DELAY)

    save_cache(cache)
    sample = list(cache["profiles"].values())
    cumulative = len(sample)
    if cumulative < MIN_VALID_TO_PUBLISH:
        raise RuntimeError(
            f"Cumulative calibration sample too small: {cumulative}; minimum required is {MIN_VALID_TO_PUBLISH}"
        )

    scores = base.metric_percentiles(base.user_daily(), sample)
    output = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "method": "cumulative_multi_event_validated_reference",
        "reference_population": "active/recent Daily Race leaderboard participants with at least 20 career Daily Races and physically valid career metrics",
        "target_valid_profiles": TARGET_VALID_REFERENCE,
        "valid_profiles": cumulative,
        "target_reached": cumulative >= TARGET_VALID_REFERENCE,
        "progress_to_target": cumulative / TARGET_VALID_REFERENCE,
        "new_valid_profiles_this_run": run_valid,
        "events_used": selected_events,
        "unique_uncached_psns_attempted": len(candidate_psns),
        "unavailable_profiles_this_run": unavailable,
        "quality_filtered_this_run": sum(rejection_counts.values()),
        "profile_request_failures_this_run": request_failures,
        "minimum_races": base.MIN_RACES,
        "validation": {
            "average_grid_range": [1, base.MAX_OBSERVED_DAILY_GRID],
            "average_finish_range": [1, base.MAX_OBSERVED_DAILY_GRID],
            "wins_lte_races": True,
            "top5_lte_races": True,
            "wins_lte_top5": True,
        },
        "rejection_reasons_this_run": dict(rejection_counts),
        "rejection_examples_this_run": rejection_examples,
        "leaderboard_failures_before_success": leaderboard_failures,
        "user_percentiles": scores,
        "sample_summary": sample_summary(sample),
    }
    base.OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    base.OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Cumulative reference after run: {cumulative}/{TARGET_VALID_REFERENCE} ({cumulative/TARGET_VALID_REFERENCE:.1%})")
    print(json.dumps(scores, indent=2))


if __name__ == "__main__":
    main()
