from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import calibrate_population as base

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_EVENT_PAGES = 3
MAX_EVENT_ATTEMPTS = 3


def candidate_events(session: requests.Session) -> list[str]:
    urls = []
    seen = set()
    for page in range(1, MAX_EVENT_PAGES + 1):
        url = base.GTSH_DAILY if page == 1 else f"{base.GTSH_DAILY}?page={page}&q="
        r = session.get(url, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.select('a[href*="/daily/leaderboard?event="], a[href*="/daily/leaderboard/?event="]')
        scored = []
        for link in links:
            href = link.get("href")
            if not href:
                continue
            full = urljoin(base.GTSH_DAILY, href)
            if full in seen:
                continue
            text = link.parent.get_text(" ", strip=True) if link.parent else ""
            priority = 0 if "Daily Race C" in text else 1 if "Daily Race B" in text else 2
            scored.append((priority, full))
            seen.add(full)
        for _, full in sorted(scored):
            urls.append(full)
    return urls


def try_sample_event(session: requests.Session, event_url: str):
    last_exc = None
    for attempt in range(1, MAX_EVENT_ATTEMPTS + 1):
        try:
            print(f"Trying leaderboard attempt {attempt}: {event_url}")
            return base.systematic_psns(session, event_url, base.TARGET_SAMPLE)
        except requests.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else None
            if status not in RETRY_STATUS:
                break
            sleep_s = 2 ** attempt
            print(f"Transient HTTP {status}; retrying in {sleep_s}s")
            time.sleep(sleep_s)
        except Exception as exc:
            last_exc = exc
            break
    raise RuntimeError(f"Leaderboard unusable: {event_url}: {last_exc}")


def main() -> None:
    session = requests.Session()
    session.headers.update(base.HEADERS)
    events = candidate_events(session)
    if not events:
        raise RuntimeError("No candidate Daily Race leaderboards found")

    chosen_event = None
    psns = None
    leaderboard_total = None
    failures = []
    for event_url in events:
        try:
            psns, leaderboard_total = try_sample_event(session, event_url)
            if psns and leaderboard_total:
                chosen_event = event_url
                print(f"Selected calibration leaderboard: {event_url}")
                break
        except Exception as exc:
            failures.append(str(exc))
            print(str(exc))

    if not chosen_event:
        raise RuntimeError("No accessible leaderboard found after trying multiple current/recent events")

    print(f"Leaderboard participants: {leaderboard_total:,}; PSNs selected: {len(psns)}")
    sample = []
    profile_failures = 0
    for i, psn in enumerate(psns, 1):
        try:
            row = base.fetch_profile(session, psn)
            if row:
                sample.append(row)
        except Exception as exc:
            profile_failures += 1
            print(f"Profile failed {psn}: {exc}")
        if i % 20 == 0:
            print(f"Profiles {i}/{len(psns)}; valid {len(sample)}")
        time.sleep(base.DELAY)

    if len(sample) < 40:
        raise RuntimeError(f"Calibration sample too small: {len(sample)} valid profiles")

    user = base.user_daily()
    scores = base.metric_percentiles(user, sample)
    output = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "method": "systematic_uniform_sample_with_multi_event_fallback",
        "reference_population": "active/recent Daily Race leaderboard participants with at least 20 career Daily Races",
        "event_url": chosen_event,
        "leaderboard_total": leaderboard_total,
        "target_sample": base.TARGET_SAMPLE,
        "selected_psns": len(psns),
        "valid_profiles": len(sample),
        "profile_failures": profile_failures,
        "minimum_races": base.MIN_RACES,
        "leaderboard_failures_before_success": failures,
        "user_percentiles": scores,
        "sample_summary": {
            key: {
                "min": min(r[key] for r in sample),
                "median": sorted(r[key] for r in sample)[len(sample)//2],
                "max": max(r[key] for r in sample),
            }
            for key in ("average_grid", "average_finish", "positions_gained_avg", "win_rate", "top5_rate")
        },
    }
    base.OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    base.OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(scores, indent=2))


if __name__ == "__main__":
    main()
