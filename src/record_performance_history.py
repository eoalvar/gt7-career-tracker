from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path("data/career.db")
RATING_PATH = Path("data/latest_rating.json")
CALIBRATION_PATH = Path("data/population_calibration.json")


def main() -> None:
    rating = json.loads(RATING_PATH.read_text(encoding="utf-8"))
    calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))

    adjusted = calibration.get("user_percentiles_dr_adjusted") or calibration.get("user_percentiles") or {}
    global_scores = calibration.get("user_percentiles_global") or {}
    captured_at = rating.get("captured_at") or calibration.get("captured_at")

    required = ("qualifying", "race_performance", "racecraft", "results", "overall")
    if not captured_at or not all(isinstance(adjusted.get(k), (int, float)) for k in required):
        raise RuntimeError("A valid calibrated performance snapshot is not available")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS performance_history (
                captured_at TEXT PRIMARY KEY,
                psn_id TEXT NOT NULL,
                driver_rating INTEGER,
                dr_label TEXT,
                dr_points REAL,
                dr_percentage REAL,
                calibration_method TEXT,
                calibration_sample_size INTEGER,
                qualifying REAL,
                race_performance REAL,
                racecraft REAL,
                results REAL,
                overall REAL,
                global_qualifying REAL,
                global_race_performance REAL,
                global_racecraft REAL,
                global_results REAL,
                global_overall REAL
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO performance_history (
                captured_at, psn_id, driver_rating, dr_label, dr_points, dr_percentage,
                calibration_method, calibration_sample_size,
                qualifying, race_performance, racecraft, results, overall,
                global_qualifying, global_race_performance, global_racecraft,
                global_results, global_overall
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                captured_at,
                rating.get("psn_id"),
                rating.get("driver_rating"),
                rating.get("dr_label"),
                rating.get("dr_points"),
                rating.get("dr_percentage"),
                calibration.get("method"),
                calibration.get("valid_profiles"),
                adjusted.get("qualifying"),
                adjusted.get("race_performance"),
                adjusted.get("racecraft"),
                adjusted.get("results"),
                adjusted.get("overall"),
                global_scores.get("qualifying"),
                global_scores.get("race_performance"),
                global_scores.get("racecraft"),
                global_scores.get("results"),
                global_scores.get("overall"),
            ),
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_performance_history_captured_at ON performance_history(captured_at)"
        )
        conn.commit()

    print(
        f"Performance snapshot recorded: DR {rating.get('dr_label')} "
        f"({rating.get('dr_points')} pts), Overall {adjusted.get('overall'):.2f}"
    )


if __name__ == "__main__":
    main()
