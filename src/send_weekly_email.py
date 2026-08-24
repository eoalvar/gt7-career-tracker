import html
import json
import os
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

RATING = Path("data/latest_rating.json")
CAREER = Path("data/latest_career.json")
CALIBRATION = Path("data/population_calibration.json")
DB = Path("data/career.db")
EMAIL_USERNAME = os.environ["EMAIL_USERNAME"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]
EMAIL_TO = os.environ["EMAIL_TO"]


def clamp(x, lo=0.0, hi=100.0): return max(lo, min(hi, x))

def grade(percentile):
    p=clamp(float(percentile))
    anchors=((0,0.0),(5,1.5),(10,2.2),(25,3.5),(50,5.0),(75,7.0),(90,8.3),(95,8.9),(99,9.6),(100,10.0))
    for (p0,g0),(p1,g1) in zip(anchors,anchors[1:]):
        if p<=p1:return g0+(p-p0)*(g1-g0)/(p1-p0)
    return 10.0

def assessment(g):
    if g>=9:return "Exceptional"
    if g>=8:return "Excellent"
    if g>=7:return "Strong"
    if g>=6:return "Above average"
    if g>=4.5:return "Competitive / average"
    if g>=3:return "Below average"
    return "Development area"

def signed(v, digits=1): return "n/a" if v is None else f"{v:+.{digits}f}"

def weekly_trend():
    if not DB.exists(): return None
    cutoff=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
    try:
        with sqlite3.connect(DB) as conn:
            conn.row_factory=sqlite3.Row
            rows=conn.execute("SELECT captured_at,dr_points,qualifying,race_performance,racecraft,results,overall FROM performance_history WHERE captured_at>=? ORDER BY captured_at",(cutoff,)).fetchall()
    except sqlite3.OperationalError:
        return None
    if len(rows)<2:return None
    a,b=rows[0],rows[-1]
    return {k:(b[k]-a[k]) for k in ("dr_points","qualifying","race_performance","racecraft","results","overall") if a[k] is not None and b[k] is not None}

def line(label, value):
    return f'<div style="padding:7px 0;border-bottom:1px solid #eeeeee"><span style="color:#666">{html.escape(str(label))}:</span> <strong>{html.escape(str(value))}</strong></div>'

def metric(name, percentile, description):
    g=grade(percentile)
    return f'''<div style="padding:14px 0;border-bottom:1px solid #e8e8e8">
<div style="font-size:17px;font-weight:700;margin-bottom:3px">{html.escape(name)} — {g:.1f}/10</div>
<div style="font-size:14px;color:#555;margin-bottom:4px">{html.escape(assessment(g))} · P{percentile:.1f}</div>
<div style="font-size:14px;line-height:1.45;color:#333">{html.escape(description)}</div>
</div>'''

def main():
    rating=json.loads(RATING.read_text(encoding="utf-8"))
    career=json.loads(CAREER.read_text(encoding="utf-8"))
    cal=json.loads(CALIBRATION.read_text(encoding="utf-8"))
    p=cal.get("user_percentiles_dr_adjusted") or cal.get("user_percentiles",{})
    daily=next((r for r in career.get("sport_types",[]) if r.get("sport_type")==1),{})
    meta=cal.get("dr_adjustment") or {}
    eff=meta.get("effective_peer_count")
    eff_text=f"{eff:.0f}" if isinstance(eff,(int,float)) else "n/a"

    metric_html="".join([
        metric("Career Rating",float(p["overall"]),"Combined competitive standing across the four career dimensions."),
        metric("Qualifying Pace",float(p["qualifying"]),"How strong your average starting position is versus competitively comparable drivers."),
        metric("Finishing Performance",float(p["race_performance"]),"How strong your average finishing position is versus competitively comparable drivers."),
        metric("Position Conversion",float(p["racecraft"]),"How effectively you convert similar starting positions into finishing positions versus comparable drivers."),
        metric("Results",float(p["results"]),"How your wins and strong finishing outcomes compare with competitively similar drivers."),
    ])

    trend=weekly_trend()
    if trend:
        trend_html="".join([
            line("Career Rating percentile",signed(trend.get("overall"))),
            line("Qualifying Pace percentile",signed(trend.get("qualifying"))),
            line("Finishing Performance percentile",signed(trend.get("race_performance"))),
            line("Position Conversion percentile",signed(trend.get("racecraft"))),
            line("Results percentile",signed(trend.get("results"))),
            line("DR points",signed(trend.get("dr_points"),0)),
        ])
    else:
        trend_html='<div style="font-size:14px;color:#666">Not enough calibrated history yet for a reliable 7-day comparison.</div>'

    body=f'''<!doctype html><html><body style="margin:0;padding:0;background:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#171717">
<div style="max-width:620px;margin:0 auto;padding:22px 18px">
<div style="font-size:12px;color:#777;text-transform:uppercase;letter-spacing:.08em">Gran Turismo 7 · Sport Mode</div>
<div style="font-size:25px;font-weight:800;margin:4px 0 2px">Weekly Career Summary</div>
<div style="font-size:14px;color:#666;margin-bottom:22px">{html.escape(rating['psn_id'])} · {datetime.now().strftime('%d %b %Y')}</div>

<div style="font-size:18px;font-weight:700;margin:0 0 7px">Current status</div>
{line('Driver Rating',f"{rating['dr_label']} — {rating['dr_points']:,} pts")}
{line('Daily Races',daily.get('races','n/a'))}
{line('Average Grid',f"{daily.get('average_grid'):.2f}" if daily.get('average_grid') is not None else 'n/a')}
{line('Average Finish',f"{daily.get('average_finish'):.2f}" if daily.get('average_finish') is not None else 'n/a')}

<div style="font-size:18px;font-weight:700;margin:26px 0 2px">Competitive ratings</div>
{metric_html}

<div style="font-size:18px;font-weight:700;margin:26px 0 7px">Last 7 days</div>
{trend_html}

<div style="font-size:18px;font-weight:700;margin:26px 0 7px">Reference population</div>
{line('Valid profiles',cal.get('valid_profiles','n/a'))}
{line('Effective peers',eff_text)}
{line('Method','Continuous cross-DR weighting')}

<div style="font-size:12px;line-height:1.45;color:#777;margin-top:25px">The 0–10 ratings are a nonlinear presentation layer. Cross-DR percentiles remain the underlying statistical benchmark.</div>
</div></body></html>'''

    msg=EmailMessage()
    msg["Subject"]="GT7 Career Tracker — Weekly Summary"
    msg["From"]=EMAIL_USERNAME
    msg["To"]=EMAIL_TO
    msg.set_content(body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(EMAIL_USERNAME,EMAIL_APP_PASSWORD)
        smtp.send_message(msg)
    print("Weekly GT7 Career Tracker mobile email sent successfully.")

if __name__=="__main__":main()
