import json
import os
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

RATING_PATH = Path("data/latest_rating.json")
CAREER_PATH = Path("data/latest_career.json")
CALIBRATION_PATH = Path("data/population_calibration.json")
DB_PATH = Path("data/career.db")

EMAIL_USERNAME = os.environ["EMAIL_USERNAME"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]
EMAIL_TO = os.environ["EMAIL_TO"]


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def grade(percentile):
    p = clamp(percentile)
    anchors = [
        (0, 0.0), (5, 1.5), (10, 2.2), (25, 3.5), (50, 5.0),
        (75, 7.0), (90, 8.3), (95, 8.9), (99, 9.6), (100, 10.0),
    ]
    for (p0, g0), (p1, g1) in zip(anchors, anchors[1:]):
        if p <= p1:
            return g0 + (p - p0) * (g1 - g0) / (p1 - p0)
    return 10.0


def assessment(value):
    if value >= 9.0: return "Exceptional"
    if value >= 8.0: return "Excellent"
    if value >= 7.0: return "Strong"
    if value >= 6.0: return "Above average"
    if value >= 4.5: return "Competitive / average"
    if value >= 3.0: return "Below average"
    return "Development area"


def weekly_trend():
    if not DB_PATH.exists():
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT captured_at, dr_points, qualifying, race_performance, racecraft, results, overall "
                "FROM performance_history WHERE captured_at >= ? ORDER BY captured_at",
                (cutoff,),
            ).fetchall()
    except sqlite3.OperationalError:
        return None
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    return {
        key: last[key] - first[key]
        for key in ("dr_points", "qualifying", "race_performance", "racecraft", "results", "overall")
        if first[key] is not None and last[key] is not None
    }


def signed(value, digits=1):
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}"


def main():
    rating = json.loads(RATING_PATH.read_text(encoding="utf-8"))
    career = json.loads(CAREER_PATH.read_text(encoding="utf-8"))
    calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    percentiles = calibration.get("user_percentiles_dr_adjusted") or calibration["user_percentiles"]
    daily = next((x for x in career.get("sport_types", []) if x.get("sport_type") == 1), {})

    metrics = [
        ("CAREER RATING", "overall", "Overall competitive standing across the four dimensions."),
        ("QUALIFYING PACE", "qualifying", "Strength of your average starting position versus comparable drivers."),
        ("FINISHING PERFORMANCE", "race_performance", "Strength of your average finishing position versus comparable drivers."),
        ("POSITION CONVERSION", "racecraft", "How well you convert your usual grid position into a finishing position."),
        ("RESULTS", "results", "Wins and strong race results versus comparable drivers."),
    ]

    lines = []
    lines.append("GT7 CAREER TRACKER")
    lines.append("WEEKLY SUMMARY")
    lines.append("")
    lines.append(f"PSN: {rating['psn_id']}")
    lines.append(f"Date: {datetime.now().strftime('%d %b %Y')}")
    lines.append("")
    lines.append("CURRENT STATUS")
    lines.append(f"Driver Rating: {rating['dr_label']}")
    lines.append(f"DR Points: {rating['dr_points']:,}")
    lines.append(f"Daily Races: {daily.get('races', 'n/a')}")
    if daily.get("average_grid") is not None:
        lines.append(f"Average Grid: {daily['average_grid']:.2f}")
    if daily.get("average_finish") is not None:
        lines.append(f"Average Finish: {daily['average_finish']:.2f}")

    lines.append("")
    lines.append("COMPETITIVE RATINGS")
    for title, key, explanation in metrics:
        p = float(percentiles[key])
        g = grade(p)
        lines.append("")
        lines.append(f"{title}: {g:.1f}/10")
        lines.append(f"{assessment(g)} | Percentile {p:.1f}")
        lines.append(explanation)

    lines.append("")
    lines.append("LAST 7 DAYS")
    trend = weekly_trend()
    if trend is None:
        lines.append("Not enough calibrated history for a reliable weekly comparison yet.")
    else:
        lines.append(f"Career Rating percentile: {signed(trend.get('overall'))}")
        lines.append(f"Qualifying percentile: {signed(trend.get('qualifying'))}")
        lines.append(f"Finishing percentile: {signed(trend.get('race_performance'))}")
        lines.append(f"Position Conversion percentile: {signed(trend.get('racecraft'))}")
        lines.append(f"Results percentile: {signed(trend.get('results'))}")
        lines.append(f"DR Points: {signed(trend.get('dr_points'), 0)}")

    meta = calibration.get("dr_adjustment") or {}
    effective = meta.get("effective_peer_count")
    lines.append("")
    lines.append("REFERENCE POPULATION")
    lines.append(f"Valid profiles: {calibration.get('valid_profiles', 'n/a')}")
    lines.append(f"Effective peers: {effective:.0f}" if isinstance(effective, (int, float)) else "Effective peers: n/a")
    lines.append("Method: Continuous cross-DR weighting")

    lines.append("")
    lines.append("NOTES")
    lines.append("The 0-10 ratings are an intuitive presentation layer. Percentiles remain the underlying statistical benchmark.")
    lines.append("Position Conversion is the presentation name for the historical racecraft metric.")

    body = "\n".join(lines)

    message = EmailMessage()
    message["Subject"] = "GT7 Career Tracker - Weekly Summary"
    message["From"] = EMAIL_USERNAME
    message["To"] = EMAIL_TO
    message.set_content(body, charset="utf-8")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USERNAME, EMAIL_APP_PASSWORD)
        smtp.send_message(message)

    print("Weekly GT7 Career Tracker plain-text email sent successfully.")


if __name__ == "__main__":
    main()
