from __future__ import annotations

import base64
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

GTSH_DAILY = "https://gtsh-rank.com/daily/"
PROFILE_BASE = "https://gtsh-rank.com/profile/?id="
OUT_PATH = Path("data/population_calibration.json")
MY_PSN = os.getenv("GT7_PSN_ID", "crazy_rooster74")
TARGET_SAMPLE = int(os.getenv("GT7_CALIBRATION_SAMPLE", "160"))
MIN_RACES = int(os.getenv("GT7_CALIBRATION_MIN_RACES", "20"))
PAGE_SIZE = 100
DELAY = float(os.getenv("GT7_CALIBRATION_DELAY", "0.08"))
HEADERS = {"User-Agent": "Mozilla/5.0 (GT7 Career Population Calibration)"}


def extract_json_variable(html: str, name: str):
    for marker in (f"const {name} = ", f"let {name} = ", f"var {name} = "):
        idx = html.find(marker)
        if idx < 0:
            continue
        idx += len(marker)
        try:
            return json.JSONDecoder().raw_decode(html[idx:].lstrip())[0]
        except Exception:
            pass
    return None


def canonical(url: str) -> str:
    p = urlparse(url)
    path = p.path.rstrip("/")
    if path.endswith("/daily/leaderboard"):
        path += "/"
    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, p.fragment))


def page_url(event_url: str, offset: int, limit: int = PAGE_SIZE) -> str:
    p = urlparse(canonical(event_url))
    q = parse_qs(p.query, keep_blank_values=True)
    q["page_data"] = ["1"]
    q["offset"] = [str(offset)]
    q["limit"] = [str(limit)]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), p.fragment))


def discover_current_event(session: requests.Session) -> str:
    r = session.get(GTSH_DAILY, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.select('a[href*="/daily/leaderboard?event="], a[href*="/daily/leaderboard/?event="]')
    # Prefer Daily Race C when available because it normally has a large, active leaderboard.
    for link in links:
        parent_text = link.parent.get_text(" ", strip=True) if link.parent else ""
        if "Daily Race C" in parent_text:
            return urljoin(GTSH_DAILY, link.get("href"))
    if links:
        return urljoin(GTSH_DAILY, links[0].get("href"))
    raise RuntimeError("No current Daily Race leaderboard found")


def initial_board(session: requests.Session, event_url: str) -> tuple[list[dict], int]:
    r = session.get(canonical(event_url), timeout=60)
    r.raise_for_status()
    initial = extract_json_variable(r.text, "initialServerPage")
    if not isinstance(initial, dict) or not isinstance(initial.get("board"), list):
        raise RuntimeError("Could not read initialServerPage from leaderboard")
    return initial["board"], int(initial.get("total", 0))


def fetch_board_page(session: requests.Session, event_url: str, offset: int) -> list[dict]:
    r = session.get(page_url(event_url, offset), headers={**HEADERS, "Accept": "application/json"}, timeout=60)
    r.raise_for_status()
    data = r.json()
    board = data.get("board") if isinstance(data, dict) else None
    return board if isinstance(board, list) else []


def online_id(driver: dict) -> str | None:
    user = driver.get("user", {})
    value = user.get("np_online_id") if isinstance(user, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def systematic_psns(session: requests.Session, event_url: str, target: int) -> tuple[list[str], int]:
    first, total = initial_board(session, event_url)
    if total <= 0:
        raise RuntimeError("Leaderboard total is zero")
    # One approximately uniform point from each equal-width interval across all participants.
    ranks = [max(1, min(total, round((i + 0.5) * total / target))) for i in range(target)]
    by_offset: dict[int, list[int]] = {}
    for rank in ranks:
        offset = ((rank - 1) // PAGE_SIZE) * PAGE_SIZE
        by_offset.setdefault(offset, []).append(rank)
    psns: list[str] = []
    seen = set()
    first_by_rank = {int(d.get("display_rank", 0)): d for d in first if d.get("display_rank")}
    for offset, wanted in sorted(by_offset.items()):
        if offset == 0:
            mapping = first_by_rank
        else:
            board = fetch_board_page(session, event_url, offset)
            mapping = {int(d.get("display_rank", 0)): d for d in board if d.get("display_rank")}
            time.sleep(DELAY)
        for rank in wanted:
            d = mapping.get(rank)
            psn = online_id(d) if d else None
            if psn and psn.lower() not in seen and psn.lower() != MY_PSN.lower():
                seen.add(psn.lower())
                psns.append(psn)
    return psns, total


def xor_decrypt(data: bytes, key: str) -> str:
    kb = key.encode("utf-8")
    return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data)).decode("utf-8")


def fetch_profile(session: requests.Session, psn: str) -> dict | None:
    url = PROFILE_BASE + psn
    page = session.get(url, timeout=30)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, "html.parser")
    body = soup.find("body")
    key = body.get("header") if body else None
    if not key:
        return None
    response = session.post(url, headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": url, "Origin": "https://gtsh-rank.com", "Accept": "application/json,text/plain,*/*"}, data={"psnid": psn}, timeout=60)
    response.raise_for_status()
    wrapper = response.json()
    enc = wrapper.get("data") if isinstance(wrapper, dict) else None
    if not isinstance(enc, str):
        return None
    payload = json.loads(xor_decrypt(base64.b64decode(enc), key))
    rows = payload.get("sport", {}).get("result", []) if isinstance(payload, dict) else []
    daily = next((r for r in rows if isinstance(r, dict) and r.get("type") == 1), None)
    if not daily:
        return None
    races = daily.get("race")
    if not isinstance(races, (int, float)) or races < MIN_RACES:
        return None
    avg_grid = daily.get("average_qualify_rank")
    avg_finish = daily.get("average_rank")
    wins = daily.get("win") or 0
    top5 = daily.get("top5") or 0
    if not isinstance(avg_grid, (int, float)) or not isinstance(avg_finish, (int, float)):
        return None
    return {
        "psn_id": psn,
        "races": int(races),
        "wins": int(wins),
        "top5": int(top5),
        "average_grid": float(avg_grid),
        "average_finish": float(avg_finish),
        "positions_gained_avg": float(avg_grid - avg_finish),
        "win_rate": float(wins / races) if races else 0.0,
        "top5_rate": float(top5 / races) if races else 0.0,
    }


def percentile(value: float, values: list[float], higher_better: bool) -> float | None:
    clean = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not clean:
        return None
    if higher_better:
        better_or_equal = sum(v <= value for v in clean)
    else:
        better_or_equal = sum(v >= value for v in clean)
    return 100.0 * better_or_equal / len(clean)


def user_daily() -> dict:
    career = json.loads(Path("data/latest_career.json").read_text(encoding="utf-8"))
    row = next(r for r in career.get("sport_types", []) if r.get("sport_type") == 1)
    return row


def metric_percentiles(user: dict, sample: list[dict]) -> dict:
    specs = {
        "average_grid": False,
        "average_finish": False,
        "positions_gained_avg": True,
        "win_rate": True,
        "top5_rate": True,
    }
    out = {}
    for key, higher in specs.items():
        out[key] = percentile(float(user[key]), [float(r[key]) for r in sample], higher)
    results = [out["win_rate"], out["top5_rate"]]
    out["results"] = sum(results) / len(results)
    out["qualifying"] = out["average_grid"]
    out["race_performance"] = out["average_finish"]
    out["racecraft"] = out["positions_gained_avg"]
    out["overall"] = 0.30 * out["qualifying"] + 0.35 * out["race_performance"] + 0.20 * out["racecraft"] + 0.15 * out["results"]
    return out


def main() -> None:
    session = requests.Session()
    session.headers.update(HEADERS)
    event_url = discover_current_event(session)
    psns, leaderboard_total = systematic_psns(session, event_url, TARGET_SAMPLE)
    print(f"Leaderboard participants: {leaderboard_total:,}; PSNs selected: {len(psns)}")
    sample = []
    failures = 0
    for i, psn in enumerate(psns, 1):
        try:
            row = fetch_profile(session, psn)
            if row:
                sample.append(row)
        except Exception as exc:
            failures += 1
            print(f"Profile failed {psn}: {exc}")
        if i % 20 == 0:
            print(f"Profiles {i}/{len(psns)}; valid {len(sample)}")
        time.sleep(DELAY)
    if len(sample) < 40:
        raise RuntimeError(f"Calibration sample too small: {len(sample)} valid profiles")
    user = user_daily()
    scores = metric_percentiles(user, sample)
    output = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "method": "systematic_uniform_sample_across_current_daily_race_leaderboard",
        "reference_population": "active Daily Race leaderboard participants with at least 20 career Daily Races",
        "event_url": event_url,
        "leaderboard_total": leaderboard_total,
        "target_sample": TARGET_SAMPLE,
        "selected_psns": len(psns),
        "valid_profiles": len(sample),
        "profile_failures": failures,
        "minimum_races": MIN_RACES,
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
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(scores, indent=2))


if __name__ == "__main__":
    main()
