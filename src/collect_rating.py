from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PSN_ID = os.getenv("GT7_PSN_ID", "crazy_rooster74")
PROFILE_URL = f"https://gtsh-rank.com/profile/?id={PSN_ID}"
DB_PATH = Path(os.getenv("GT7_CAREER_DB", "data/career.db"))
LATEST_PATH = Path("data/latest_rating.json")
DR_LABELS = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A", 6: "A+", 7: "S"}


def xor_decrypt(data: bytes, key: str) -> str:
    key_bytes = key.encode("utf-8")
    decoded = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
    return decoded.decode("utf-8")


def fetch_profile(session: requests.Session) -> dict:
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


def initialize_database(conn: sqlite3.Connection) -> None:
    conn.execute(
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
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rating_history_captured_at ON rating_history(captured_at)"
    )
    conn.commit()


def save_profile(profile: dict) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        initialize_database(conn)
        conn.execute(
            """
            INSERT INTO rating_history (
                captured_at, psn_id, driver_rating, dr_label, dr_points,
                dr_percentage, sportsmanship_rating, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile["captured_at"],
                profile["psn_id"],
                profile["driver_rating"],
                profile["dr_label"],
                profile["dr_points"],
                profile["dr_percentage"],
                profile["sportsmanship_rating"],
                profile["source"],
            ),
        )
        conn.commit()

    LATEST_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (GT7 Career Tracker)"})

    profile = fetch_profile(session)
    save_profile(profile)

    print(f"PSN: {profile['psn_id']}")
    print(
        f"DR: {profile['dr_label']} | {profile['dr_points']} pts | "
        f"{profile['dr_percentage']}% toward next DR"
    )
    print(f"SR: {profile['sportsmanship_rating']}")
    print(f"Captured: {profile['captured_at']}")


if __name__ == "__main__":
    main()
