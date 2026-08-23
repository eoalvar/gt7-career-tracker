from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

RATING_PATH = Path("data/latest_rating.json")
CAREER_PATH = Path("data/latest_career.json")
REPORT_PATH = Path("reports/latest.md")


def pct(value):
    return "n/a" if value is None else f"{value * 100:.2f}%"


def num(value, digits=2):
    return "n/a" if value is None else f"{value:.{digits}f}"


def main():
    rating = json.loads(RATING_PATH.read_text(encoding="utf-8"))
    career = json.loads(CAREER_PATH.read_text(encoding="utf-8"))
    lines = [
        "# GT7 Sport Career Report",
        "",
        f"PSN: **{rating['psn_id']}**  ",
        f"Updated: {career['captured_at']}  ",
        f"DR: **{rating['dr_label']}** — {rating['dr_points']} points — {rating['dr_percentage']}% toward next DR  ",
        f"SR: **{rating['sportsmanship_rating']}**",
        "",
        "## Sport career",
        "",
        "| Type | Races | Wins | Top 5 | Poles | Avg grid | Avg finish | Avg positions gained | Win rate | Top-5 rate | Pole rate |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in career.get("sport_types", []):
        lines.append(
            f"| {row['sport_type']} | {row['races']} | {row['wins']} | {row['top5']} | {row['poles']} | "
            f"{num(row['average_grid'])} | {num(row['average_finish'])} | {num(row['positions_gained_avg'])} | "
            f"{pct(row['win_rate'])} | {pct(row['top5_rate'])} | {pct(row['pole_rate'])} |"
        )

    sm = career.get("sports_mode", {})
    qp = career.get("qualifying_performance", {})
    lines += [
        "",
        "## Sports Mode counters",
        "",
        f"Races: **{sm.get('race_count')}** · Wins: **{sm.get('win_count')}** · Poles: **{sm.get('pole_position_count')}** · "
        f"Fastest laps: **{sm.get('fastest_lap_count')}** · Clean races: **{sm.get('clean_race_count')}**",
        "",
        "## Qualifying performance",
        "",
        f"Best rank: **{qp.get('best_rank')}** · Median rank: **{qp.get('median_rank')}** · "
        f"Average rank: **{qp.get('average_rank')}** · Worst rank: **{qp.get('worst_rank')}** · "
        f"Rank standard deviation: **{qp.get('rank_stddev')}**",
        "",
        "## Interpretation",
        "",
    ]

    for row in career.get("sport_types", []):
        gained = row.get("positions_gained_avg")
        if gained is not None:
            direction = "gains" if gained >= 0 else "loses"
            lines.append(
                f"- Sport type {row['sport_type']}: on average {direction} **{abs(gained):.2f} positions per race** "
                f"from qualifying/grid position to finish."
            )
    lines.append("- Sport type labels remain numeric until their exact GTSH semantics are verified.")
    lines.append("- GTSH Sport totals and monthly Sports Mode counters are kept separate because they currently use different definitions/populations.")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Career report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
