from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

RATING_PATH = Path("data/latest_rating.json")
CAREER_PATH = Path("data/latest_career.json")
DB_PATH = Path("data/career.db")
REPORT_PATH = Path("reports/latest.md")

# GT7 Sport statistics use type 1 for Daily Races and type 2 for championship races.
# Keep the raw numeric sport_type in the database; labels are presentation-only.
SPORT_TYPE_LABELS = {1: "Daily Races", 2: "Championships"}


def sport_label(value):
    return SPORT_TYPE_LABELS.get(int(value), f"Sport type {value}")


def pct(value):
    return "n/a" if value is None else f"{value * 100:.2f}%"


def num(value, digits=2):
    return "n/a" if value is None else f"{value:.{digits}f}"


def delta(current, previous):
    if current is None or previous is None:
        return None
    return current - previous


def signed(value, digits=2):
    return "n/a" if value is None else f"{value:+.{digits}f}"


def date_span_days(start: str, end: str) -> int:
    start_date = datetime.fromisoformat(start.replace("Z", "+00:00")).date()
    end_date = datetime.fromisoformat(end.replace("Z", "+00:00")).date()
    return max(0, (end_date - start_date).days)


def coverage_status(requested_days: int, covered_days: int) -> str:
    if covered_days <= 0:
        return "insufficient_data"
    if covered_days < max(2, int(requested_days * 0.8)):
        return "partial_coverage"
    return "ok"


def load_period_trends(days: int) -> list[dict]:
    if not DB_PATH.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    trends = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        types = [r[0] for r in conn.execute("SELECT DISTINCT sport_type FROM sport_daily_history ORDER BY sport_type")]
        for sport_type in types:
            rows = conn.execute(
                "SELECT * FROM sport_daily_history WHERE sport_type = ? AND date >= ? ORDER BY date ASC",
                (sport_type, cutoff),
            ).fetchall()
            if len(rows) < 2:
                trends.append({"sport_type": sport_type, "days": days, "status": "insufficient_data"})
                continue
            first, last = rows[0], rows[-1]
            covered_days = date_span_days(first["date"], last["date"])
            races_delta = delta(last["races"], first["races"])
            wins_delta = delta(last["wins"], first["wins"])
            top5_delta = delta(last["top5"], first["top5"])
            poles_delta = delta(last["poles"], first["poles"])
            trends.append({
                "sport_type": sport_type,
                "days": days,
                "status": coverage_status(days, covered_days),
                "covered_days": covered_days,
                "races": races_delta,
                "wins": wins_delta,
                "top5": top5_delta,
                "poles": poles_delta,
                "avg_grid_change": delta(last["average_grid"], first["average_grid"]),
                "avg_finish_change": delta(last["average_finish"], first["average_finish"]),
                "period_win_rate": (wins_delta / races_delta) if races_delta and races_delta > 0 and wins_delta is not None else None,
                "period_top5_rate": (top5_delta / races_delta) if races_delta and races_delta > 0 and top5_delta is not None else None,
            })
    return trends


def load_dr_trend(days: int) -> dict:
    if not DB_PATH.exists():
        return {"status": "insufficient_data"}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT captured_at, dr_points, dr_percentage, driver_rating, dr_label FROM rating_history WHERE captured_at >= ? ORDER BY captured_at ASC",
            (cutoff,),
        ).fetchall()
    if len(rows) < 2:
        return {"status": "insufficient_data"}
    first, last = rows[0], rows[-1]
    covered_days = date_span_days(first["captured_at"], last["captured_at"])
    return {
        "status": coverage_status(days, covered_days),
        "covered_days": covered_days,
        "dr_points_change": delta(last["dr_points"], first["dr_points"]),
        "dr_percentage_change": delta(last["dr_percentage"], first["dr_percentage"]),
        "from_label": first["dr_label"],
        "to_label": last["dr_label"],
    }


def main():
    rating = json.loads(RATING_PATH.read_text(encoding="utf-8"))
    career = json.loads(CAREER_PATH.read_text(encoding="utf-8"))
    lines = [
        "# GT7 Sport Career Report", "",
        f"PSN: **{rating['psn_id']}**  ",
        f"Updated: {career['captured_at']}  ",
        f"DR: **{rating['dr_label']}** — {rating['dr_points']} points — {rating['dr_percentage']}% toward next DR  ",
        f"SR: **{rating['sportsmanship_rating']}", "",
        "## Sport career", "",
        "| Category | Races | Wins | Top 5 | Poles | Avg grid | Avg finish | Avg positions gained | Win rate | Top-5 rate | Pole rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in career.get("sport_types", []):
        lines.append(
            f"| {sport_label(row['sport_type'])} | {row['races']} | {row['wins']} | {row['top5']} | {row['poles']} | "
            f"{num(row['average_grid'])} | {num(row['average_finish'])} | {num(row['positions_gained_avg'])} | "
            f"{pct(row['win_rate'])} | {pct(row['top5_rate'])} | {pct(row['pole_rate'])} |"
        )

    sm = career.get("sports_mode", {})
    qp = career.get("qualifying_performance", {})
    lines += ["", "## Sports Mode counters", "",
        f"Races: **{sm.get('race_count')}** · Wins: **{sm.get('win_count')}** · Poles: **{sm.get('pole_position_count')}** · Fastest laps: **{sm.get('fastest_lap_count')}** · Clean races: **{sm.get('clean_race_count')}**",
        "", "## Qualifying performance", "",
        f"Best rank: **{qp.get('best_rank')}** · Median rank: **{qp.get('median_rank')}** · Average rank: **{qp.get('average_rank')}** · Worst rank: **{qp.get('worst_rank')}** · Rank standard deviation: **{qp.get('rank_stddev')}**",
        "", "## Trends", ""]

    for days in (7, 30, 90):
        dr = load_dr_trend(days)
        lines.append(f"### Last {days} days")
        if dr.get("status") == "insufficient_data":
            lines.append("DR: insufficient history for a reliable trend.")
        else:
            coverage_note = "" if dr.get("status") == "ok" else f" · partial coverage: {dr.get('covered_days', 0)} days"
            lines.append(f"DR: {dr['from_label']} → {dr['to_label']} · points {signed(dr['dr_points_change'], 0)} · progress {signed(dr['dr_percentage_change'], 1)} pp{coverage_note}")
        for tr in load_period_trends(days):
            label = sport_label(tr['sport_type'])
            if tr.get("status") == "insufficient_data":
                lines.append(f"- {label}: insufficient history.")
                continue
            coverage_note = "" if tr.get("status") == "ok" else f" · partial coverage: {tr.get('covered_days', 0)} days"
            lines.append(f"- {label}: {tr['races']} races · {tr['wins']} wins · {tr['top5']} Top 5 · {tr['poles']} poles · win rate {pct(tr['period_win_rate'])} · Top-5 rate {pct(tr['period_top5_rate'])} · avg grid change {signed(tr['avg_grid_change'])} · avg finish change {signed(tr['avg_finish_change'])}{coverage_note}")
        lines.append("")

    lines += ["## Interpretation", ""]
    for row in career.get("sport_types", []):
        gained = row.get("positions_gained_avg")
        if gained is not None:
            direction = "gains" if gained >= 0 else "loses"
            lines.append(f"- {sport_label(row['sport_type'])}: on average {direction} **{abs(gained):.2f} positions per race** from qualifying/grid position to finish.")
    lines.append("- Positive grid/finish change means the numerical average position increased; because lower position numbers are better, negative changes indicate improvement.")
    lines.append("- Trend windows explicitly flag partial coverage until the database contains enough history for the requested 7/30/90-day period.")
    lines.append("- Raw GTSH sport_type values remain stored unchanged in SQLite; human-readable labels are applied only in reports.")
    lines.append("- GTSH Sport totals and monthly Sports Mode counters are kept separate because they currently use different definitions/populations.")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Career report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
