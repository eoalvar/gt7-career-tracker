from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

db = sqlite3.connect("data/career.db")
db.row_factory = sqlite3.Row
out = ["# DR versus race activity audit", "", f"Generated UTC: {datetime.now(timezone.utc).isoformat()}", "", "Daily history values are cumulative snapshots, not individual race results.", ""]
for table in ("sport_daily_history", "sport_career_snapshot", "rating_history"):
    out += [f"## {table}", ""]
    if table == "sport_daily_history":
        sql = "SELECT date, sport_type, races, wins, top5, poles FROM sport_daily_history WHERE date >= '2026-10-09' ORDER BY date, sport_type"
    elif table == "sport_career_snapshot":
        sql = "SELECT captured_at, sport_type, races, wins, top5, poles FROM sport_career_snapshot WHERE captured_at >= '2026-10-11' ORDER BY captured_at"
    else:
        sql = "SELECT captured_at, dr_label, dr_points, dr_percentage FROM rating_history WHERE captured_at >= '2026-10-11' ORDER BY captured_at"
    rows = db.execute(sql).fetchall()
    if not rows:
        out += ["No records for period.", ""]
        continue
    out += [" | ".join(rows[0].keys()), " | ".join(["---"] * len(rows[0].keys()))]
    for row in rows:
        out.append(" | ".join(str(v) if v is not None else "n/a" for v in row))
    out.append("")
Path("reports/dr_race_sync_audit.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("Wrote reports/dr_race_sync_audit.md")
