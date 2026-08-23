from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

PSN_ID = os.getenv("GT7_PSN_ID", "crazy_rooster74")
PROFILE_URL = f"https://gtsh-rank.com/profile/?id={PSN_ID}"
DB_PATH = Path(os.getenv("GT7_CAREER_DB", "data/career.db"))
LATEST_PATH = Path("data/latest_rating.json")
LATEST_CAREER_PATH = Path("data/latest_career.json")
DIAGNOSTIC_PATH = Path("data/latest_profile_fields.json")
DR_LABELS = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A", 6: "A+", 7: "S"}
CAREER_KEY_RE = re.compile(
    r"(win|race|pole|lap|rank|qual|top|finish|start|rating|sport|lead|position|podium)",
    re.IGNORECASE,
)


def xor_decrypt(data: bytes, key: str) -> str:
    key_bytes = key.encode("utf-8")
    decoded = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
    return decoded.decode("utf-8")


def fetch_payload(session: requests.Session) -> dict:
    page = session.get(PROFILE_URL, timeout=30)
    page.raise_for_status()

    soup = BeautifulSoup(page.text, "html.parser")
    body = soup.find("body")
    key = body.get("header") if body else None
    if not key:
        raise RuntimeError("GTSH profile encryption key was not found")

    response = session.post(
        PROFILE_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": PROFILE_URL,
            "Origin": "https://gtsh-rank.com",
            "Accept": "application/json,text/plain,*/*",
        },
        data={"psnid": PSN_ID},
        timeout=60,
    )
    response.raise_for_status()

    wrapper = response.json()
    encrypted = wrapper.get("data") if isinstance(wrapper, dict) else None
    if not isinstance(encrypted, str):
        raise RuntimeError("GTSH returned an unexpected response")

    payload = json.loads(xor_decrypt(base64.b64decode(encrypted), key))
    if not isinstance(payload, dict):
        raise RuntimeError("GTSH decrypted payload was not an object")
    return payload


def parse_rating(payload: dict) -> dict:
    user = payload.get("monthly_stats", {}).get("result", {}).get("user")
    if not isinstance(user, dict):
        raise RuntimeError("GTSH user profile was not found in response")

    dr_code = user.get("driver_rating")
    dr_code = int(dr_code) if isinstance(dr_code, (int, float)) else None

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "psn_id": user.get("np_online_id") or PSN_ID,
        "driver_rating": dr_code,
        "dr_label": user.get("dr_level") or DR_LABELS.get(dr_code),
        "dr_points": user.get("dr_points"),
        "dr_percentage": user.get("dr_percentage"),
        "sportsmanship_rating": user.get("sportsmanship_rating"),
        "source": "GTSH public profile",
    }


def normalize_sport_row(row: dict) -> dict:
    races = row.get("race")
    wins = row.get("win")
    top5 = row.get("top5")
    poles = row.get("pole_position")
    avg_grid = row.get("average_qualify_rank")
    avg_finish = row.get("average_rank")
    return {
        "sport_type": row.get("type"),
        "races": races,
        "wins": wins,
        "top5": top5,
        "poles": poles,
        "laps": row.get("lap"),
        "lead_laps": row.get("lead_lap"),
        "average_grid": avg_grid,
        "average_finish": avg_finish,
        "positions_gained_avg": (avg_grid - avg_finish)
        if isinstance(avg_grid, (int, float)) and isinstance(avg_finish, (int, float))
        else None,
        "win_rate": (wins / races) if isinstance(wins, (int, float)) and races else None,
        "top5_rate": (top5 / races) if isinstance(top5, (int, float)) and races else None,
        "pole_rate": (poles / races) if isinstance(poles, (int, float)) and races else None,
    }


def parse_career(payload: dict, captured_at: str) -> dict:
    sport_result = payload.get("sport", {}).get("result", [])
    if not isinstance(sport_result, list):
        sport_result = []

    sport_types = [normalize_sport_row(row) for row in sport_result if isinstance(row, dict)]

    monthly = payload.get("monthly_stats", {}).get("result", {}).get("sports_mode", {})
    if not isinstance(monthly, dict):
        monthly = {}

    performance = payload.get("stats", {}).get("performance", {})
    if not isinstance(performance, dict):
        performance = {}

    return {
        "captured_at": captured_at,
        "psn_id": PSN_ID,
        "sport_types": sport_types,
        "sports_mode": {
            "race_count": monthly.get("race_count"),
            "win_count": monthly.get("win_count"),
            "pole_position_count": monthly.get("pole_position_count"),
            "fastest_lap_count": monthly.get("fastest_lap_count"),
            "clean_race_count": monthly.get("clean_race_count"),
        },
        "qualifying_performance": {
            "best_rank": performance.get("best_rank"),
            "average_rank": performance.get("average_rank"),
            "median_rank": performance.get("median_rank"),
            "worst_rank": performance.get("worst_rank"),
            "rank_stddev": performance.get("rank_stddev"),
            "rank_distribution": performance.get("rank_distribution"),
        },
        "source": "GTSH public profile",
    }


def flatten_candidate_fields(value: Any, path: str = "", depth: int = 0) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if depth > 7:
        return result

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            result.update(flatten_candidate_fields(child, child_path, depth + 1))
    elif isinstance(value, list):
        if CAREER_KEY_RE.search(path):
            result[f"{path}.__count__"] = len(value)
        for index, child in enumerate(value[:3]):
            result.update(flatten_candidate_fields(child, f"{path}[{index}]", depth + 1))
    elif value is None or isinstance(value, (str, int, float, bool)):
        if CAREER_KEY_RE.search(path):
            result[path] = value

    return result


def save_diagnostics(payload: dict, captured_at: str) -> None:
    DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    diagnostic = {
        "captured_at": captured_at,
        "psn_id": PSN_ID,
        "top_level_keys": sorted(payload.keys()),
        "candidate_fields": flatten_candidate_fields(payload),
    }
    DIAGNOSTIC_PATH.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def initialize_database(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rating_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL UNIQUE,
            psn_id TEXT NOT NULL,
            driver_rating INTEGER,
            dr_label TEXT,
            dr_points REAL,
            dr_percentage REAL,
            sportsmanship_rating TEXT,
            source TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_rating_history_captured_at
        ON rating_history(captured_at);

        CREATE TABLE IF NOT EXISTS sport_career_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            psn_id TEXT NOT NULL,
            sport_type INTEGER NOT NULL,
            races INTEGER,
            wins INTEGER,
            top5 INTEGER,
            poles INTEGER,
            laps INTEGER,
            lead_laps INTEGER,
            average_grid REAL,
            average_finish REAL,
            positions_gained_avg REAL,
            win_rate REAL,
            top5_rate REAL,
            pole_rate REAL,
            UNIQUE(captured_at, sport_type)
        );

        CREATE TABLE IF NOT EXISTS sport_daily_history (
            date TEXT NOT NULL,
            sport_type INTEGER NOT NULL,
            races INTEGER,
            wins INTEGER,
            top5 INTEGER,
            poles INTEGER,
            laps INTEGER,
            lead_laps INTEGER,
            average_grid REAL,
            average_finish REAL,
            PRIMARY KEY(date, sport_type)
        );
        """
    )
    conn.commit()


def upsert_history(conn: sqlite3.Connection, payload: dict, key: str, sport_type: int) -> None:
    history = payload.get(key, [])
    if not isinstance(history, list):
        return
    nested_key = f"sport_type{sport_type}"
    for item in history:
        if not isinstance(item, dict) or not item.get("date"):
            continue
        row = item.get(nested_key)
        if not isinstance(row, dict):
            continue
        conn.execute(
            """
            INSERT INTO sport_daily_history (
                date, sport_type, races, wins, top5, poles, laps, lead_laps,
                average_grid, average_finish
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, sport_type) DO UPDATE SET
                races=excluded.races, wins=excluded.wins, top5=excluded.top5,
                poles=excluded.poles, laps=excluded.laps, lead_laps=excluded.lead_laps,
                average_grid=excluded.average_grid, average_finish=excluded.average_finish
            """,
            (
                item["date"], sport_type, row.get("race"), row.get("win"),
                row.get("top5"), row.get("pole_position"), row.get("lap"),
                row.get("lead_lap"), row.get("average_qualify_rank"), row.get("average_rank"),
            ),
        )


def save_all(profile: dict, career: dict, payload: dict) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        initialize_database(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO rating_history (
                captured_at, psn_id, driver_rating, dr_label, dr_points,
                dr_percentage, sportsmanship_rating, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile["captured_at"], profile["psn_id"], profile["driver_rating"],
                profile["dr_label"], profile["dr_points"], profile["dr_percentage"],
                profile["sportsmanship_rating"], profile["source"],
            ),
        )

        for row in career["sport_types"]:
            conn.execute(
                """
                INSERT OR IGNORE INTO sport_career_snapshot (
                    captured_at, psn_id, sport_type, races, wins, top5, poles,
                    laps, lead_laps, average_grid, average_finish,
                    positions_gained_avg, win_rate, top5_rate, pole_rate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    career["captured_at"], career["psn_id"], row["sport_type"],
                    row["races"], row["wins"], row["top5"], row["poles"], row["laps"],
                    row["lead_laps"], row["average_grid"], row["average_finish"],
                    row["positions_gained_avg"], row["win_rate"], row["top5_rate"], row["pole_rate"],
                ),
            )

        upsert_history(conn, payload, "sport_type1_history", 1)
        upsert_history(conn, payload, "sport_type2_history", 2)
        conn.commit()

    LATEST_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LATEST_CAREER_PATH.write_text(json.dumps(career, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (GT7 Career Tracker)"})

    payload = fetch_payload(session)
    profile = parse_rating(payload)
    career = parse_career(payload, profile["captured_at"])
    save_all(profile, career, payload)
    save_diagnostics(payload, profile["captured_at"])

    print(f"PSN: {profile['psn_id']}")
    print(f"DR: {profile['dr_label']} | {profile['dr_points']} pts | {profile['dr_percentage']}% toward next DR")
    print(f"SR: {profile['sportsmanship_rating']}")
    for row in career["sport_types"]:
        print(
            f"Sport type {row['sport_type']}: {row['races']} races | {row['wins']} wins | "
            f"avg grid {row['average_grid']:.2f} | avg finish {row['average_finish']:.2f}"
        )
    print(f"Captured: {profile['captured_at']}")


if __name__ == "__main__":
    main()
