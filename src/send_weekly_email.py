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
    p=clamp(float(percentile)); anchors=((0,0.0),(5,1.5),(10,2.2),(25,3.5),(50,5.0),(75,7.0),(90,8.3),(95,8.9),(99,9.6),(100,10.0))
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
    except sqlite3.OperationalError:return None
    if len(rows)<2:return None
    a,b=rows[0],rows[-1]
    return {k:(b[k]-a[k]) for k in ("dr_points","qualifying","race_performance","racecraft","results","overall") if a[k] is not None and b[k] is not None}

def td(x, bold=False):
    tag="strong" if bold else "span"
    return f'<td style="padding:10px 8px;border-bottom:1px solid #e6e6e6;vertical-align:top"><{tag}>{html.escape(str(x))}</{tag}></td>'

def main():
    rating=json.loads(RATING.read_text(encoding="utf-8")); career=json.loads(CAREER.read_text(encoding="utf-8")); cal=json.loads(CALIBRATION.read_text(encoding="utf-8"))
    p=cal.get("user_percentiles_dr_adjusted") or cal.get("user_percentiles",{})
    metrics=(("Career Rating","overall","Combined competitive standing"),("Qualifying Pace","qualifying","Starting-position strength versus comparable drivers"),("Finishing Performance","race_performance","Finishing-position strength versus comparable drivers"),("Position Conversion","racecraft","Conversion of similar grid positions into finishing positions"),("Results","results","Wins and strong finishing outcomes versus comparable drivers"))
    rows=[]
    for name,key,desc in metrics:
        pct=float(p[key]); g=grade(pct); rows.append(f"<tr>{td(name,True)}{td(f'{g:.1f} / 10',True)}{td(assessment(g))}{td(f'P{pct:.1f} — {desc}')}</tr>")
    daily=next((r for r in career.get("sport_types",[]) if r.get("sport_type")==1),{})
    trend=weekly_trend()
    if trend:
        trend_rows="".join([
            f"<tr>{td('Career Rating')}{td(signed(trend.get('overall')))}</tr>",
            f"<tr>{td('Qualifying Pace')}{td(signed(trend.get('qualifying')))}</tr>",
            f"<tr>{td('Finishing Performance')}{td(signed(trend.get('race_performance')))}</tr>",
            f"<tr>{td('Position Conversion')}{td(signed(trend.get('racecraft')))}</tr>",
            f"<tr>{td('Results')}{td(signed(trend.get('results')))}</tr>",
            f"<tr>{td('DR points')}{td(signed(trend.get('dr_points'),0))}</tr>"])
        trend_block=f'<h2 style="font-size:18px;margin:28px 0 8px">Last 7 days</h2><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#fff;border:1px solid #ddd"><tr style="background:#f4f4f4">{td("Metric",True)}{td("Change",True)}</tr>{trend_rows}</table>'
    else:
        trend_block='<h2 style="font-size:18px;margin:28px 0 8px">Last 7 days</h2><p style="margin:0;color:#666">Not enough calibrated history yet for a reliable weekly comparison.</p>'
    meta=cal.get("dr_adjustment") or {}; eff=meta.get("effective_peer_count"); eff_text=f"{eff:.0f}" if isinstance(eff,(int,float)) else "n/a"
    body=f'''<!doctype html><html><body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#171717"><div style="max-width:720px;margin:0 auto;padding:24px 12px"><div style="background:#ffffff;border:1px solid #dedede;border-radius:12px;padding:24px"><div style="font-size:12px;letter-spacing:.08em;color:#666;text-transform:uppercase">Gran Turismo 7 · Sport Mode</div><h1 style="font-size:26px;margin:5px 0 4px">Weekly Career Summary</h1><p style="margin:0 0 22px;color:#666">{html.escape(rating['psn_id'])} · {datetime.now().strftime('%d %b %Y')}</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#fafafa;border:1px solid #ddd"><tr>{td('Driver Rating',True)}{td(f"{rating['dr_label']} — {rating['dr_points']:,} pts",True)}</tr><tr>{td('Daily Races')}{td(daily.get('races','n/a'))}</tr><tr>{td('Average Grid')}{td(f"{daily.get('average_grid',0):.2f}" if daily.get('average_grid') is not None else 'n/a')}</tr><tr>{td('Average Finish')}{td(f"{daily.get('average_finish',0):.2f}" if daily.get('average_finish') is not None else 'n/a')}</tr></table>
<h2 style="font-size:18px;margin:28px 0 8px">Competitive ratings</h2><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#fff;border:1px solid #ddd"><tr style="background:#f4f4f4">{td('Dimension',True)}{td('Rating',True)}{td('Assessment',True)}{td('Context',True)}</tr>{''.join(rows)}</table>
{trend_block}
<h2 style="font-size:18px;margin:28px 0 8px">Reference population</h2><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#fff;border:1px solid #ddd"><tr>{td('Valid profiles')}{td(cal.get('valid_profiles','n/a'))}</tr><tr>{td('Effective peers')}{td(eff_text)}</tr><tr>{td('Method')}{td('Continuous cross-DR weighting')}</tr></table>
<p style="font-size:12px;line-height:1.5;color:#777;margin:28px 0 0">Ratings use a nonlinear 0–10 presentation scale. Cross-DR percentiles remain the underlying statistical benchmark. Position Conversion is the presentation name for the historical racecraft field.</p></div></div></body></html>'''
    msg=EmailMessage()
    msg["Subject"]="GT7 Career Tracker — HTML TEST"
    msg["From"]=EMAIL_USERNAME
    msg["To"]=EMAIL_TO
    msg.set_content(body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(EMAIL_USERNAME,EMAIL_APP_PASSWORD); smtp.send_message(msg)
    print("Weekly GT7 Career Tracker HTML-only email sent successfully.")

if __name__=="__main__":main()
