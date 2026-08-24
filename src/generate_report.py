from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

RATING_PATH = Path("data/latest_rating.json")
CAREER_PATH = Path("data/latest_career.json")
CALIBRATION_PATH = Path("data/population_calibration.json")
DB_PATH = Path("data/career.db")
REPORT_PATH = Path("reports/latest.md")
SPORT_TYPE_LABELS = {1: "Daily Races", 2: "Championships"}


def sport_label(value): return SPORT_TYPE_LABELS.get(int(value), f"Sport type {value}")
def pct(value): return "n/a" if value is None else f"{value * 100:.2f}%"
def num(value, digits=2): return "n/a" if value is None else f"{value:.{digits}f}"
def delta(current, previous): return None if current is None or previous is None else current - previous
def signed(value, digits=2): return "n/a" if value is None else f"{value:+.{digits}f}"
def clamp(x, lo=0.0, hi=100.0): return max(lo, min(hi, x))

def competitive_grade(percentile):
    """Convert population percentile to an intuitive 0-10 competitive grade.

    The scale is deliberately nonlinear so that 5.0 is median, 7+ is strong,
    8+ is excellent, 9+ is exceptional, and 10 is reserved for the extreme tail.
    """
    p=clamp(float(percentile),0.0,100.0)
    anchors=((0,0.0),(5,1.5),(10,2.2),(25,3.5),(50,5.0),(75,7.0),(90,8.3),(95,8.9),(99,9.6),(100,10.0))
    for (p0,g0),(p1,g1) in zip(anchors,anchors[1:]):
        if p<=p1:
            return g0+(p-p0)*(g1-g0)/(p1-p0)
    return 10.0

def grade_label(grade):
    if grade>=9:return "Exceptional"
    if grade>=8:return "Excellent"
    if grade>=7:return "Strong"
    if grade>=6:return "Above average"
    if grade>=4.5:return "Competitive / average"
    if grade>=3:return "Below average"
    return "Development area"

def metric_explanation(name, percentile):
    peer=f"P{percentile:.1f}"
    texts={
        "Qualifying Pace":f"{peer}: starting-position performance versus competitively comparable drivers.",
        "Finishing Performance":f"{peer}: finishing-position performance versus competitively comparable drivers.",
        "Position Conversion":f"{peer}: ability to convert similar grid positions into finishing positions versus comparable drivers.",
        "Results":f"{peer}: concrete race outcomes such as wins and Top-5 results versus comparable drivers.",
        "Career Rating":f"{peer}: combined competitive standing across the four career dimensions.",
    }
    return texts[name]

def date_span_days(start, end):
    a=datetime.fromisoformat(start.replace("Z","+00:00")).date(); b=datetime.fromisoformat(end.replace("Z","+00:00")).date(); return max(0,(b-a).days)
def coverage_status(requested, covered):
    if covered<=0:return "insufficient_data"
    return "partial_coverage" if covered < max(2,int(requested*.8)) else "ok"

def load_period_trends(days):
    if not DB_PATH.exists(): return []
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).date().isoformat(); out=[]
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory=sqlite3.Row
        types=[r[0] for r in conn.execute("SELECT DISTINCT sport_type FROM sport_daily_history ORDER BY sport_type")]
        for st in types:
            rows=conn.execute("SELECT * FROM sport_daily_history WHERE sport_type=? AND date>=? ORDER BY date ASC",(st,cutoff)).fetchall()
            if len(rows)<2: out.append({"sport_type":st,"status":"insufficient_data"}); continue
            a,b=rows[0],rows[-1]; covered=date_span_days(a['date'],b['date']); races=delta(b['races'],a['races']); wins=delta(b['wins'],a['wins']); top5=delta(b['top5'],a['top5']); poles=delta(b['poles'],a['poles'])
            out.append({"sport_type":st,"status":coverage_status(days,covered),"covered_days":covered,"races":races,"wins":wins,"top5":top5,"poles":poles,"avg_grid_change":delta(b['average_grid'],a['average_grid']),"avg_finish_change":delta(b['average_finish'],a['average_finish']),"period_win_rate":wins/races if races and races>0 else None,"period_top5_rate":top5/races if races and races>0 else None})
    return out

def load_dr_trend(days):
    if not DB_PATH.exists(): return {"status":"insufficient_data"}
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory=sqlite3.Row; rows=conn.execute("SELECT captured_at,dr_points,dr_percentage,dr_label FROM rating_history WHERE captured_at>=? ORDER BY captured_at",(cutoff,)).fetchall()
    if len(rows)<2:return {"status":"insufficient_data"}
    a,b=rows[0],rows[-1]; covered=date_span_days(a['captured_at'],b['captured_at'])
    return {"status":coverage_status(days,covered),"covered_days":covered,"dr_points_change":delta(b['dr_points'],a['dr_points']),"dr_percentage_change":delta(b['dr_percentage'],a['dr_percentage']),"from_label":a['dr_label'],"to_label":b['dr_label']}

def load_performance_trend(days):
    if not DB_PATH.exists(): return {"status":"insufficient_data"}
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory=sqlite3.Row
            rows=conn.execute("SELECT captured_at,dr_points,qualifying,race_performance,racecraft,results,overall FROM performance_history WHERE captured_at>=? ORDER BY captured_at ASC",(cutoff,)).fetchall()
    except sqlite3.OperationalError:return {"status":"insufficient_data"}
    if len(rows)<2:return {"status":"insufficient_data","snapshot_count":len(rows)}
    a,b=rows[0],rows[-1]; covered=date_span_days(a['captured_at'],b['captured_at'])
    return {"status":coverage_status(days,covered),"covered_days":covered,"snapshot_count":len(rows),"dr_points_change":delta(b['dr_points'],a['dr_points']),"qualifying_change":delta(b['qualifying'],a['qualifying']),"race_performance_change":delta(b['race_performance'],a['race_performance']),"racecraft_change":delta(b['racecraft'],a['racecraft']),"results_change":delta(b['results'],a['results']),"overall_change":delta(b['overall'],a['overall']),"overall_start":a['overall'],"overall_end":b['overall']}

def provisional_indices(career):
    daily=next((r for r in career.get('sport_types',[]) if r.get('sport_type')==1),None); sm=career.get('sports_mode',{})
    if not daily:return {}
    avg_grid=daily.get('average_grid'); avg_finish=daily.get('average_finish'); gained=daily.get('positions_gained_avg') or 0; top5=daily.get('top5_rate') or 0; win=daily.get('win_rate') or 0
    qualifying=clamp(100-(max(1,avg_grid)-1)*10) if avg_grid is not None else None; finish_score=clamp(100-(max(1,avg_finish)-1)*10) if avg_finish is not None else None; advancement=clamp(50+gained*15)
    race_performance=clamp(.55*finish_score+.30*advancement+.15*clamp(top5*100)) if finish_score is not None else None; clean_rate=(sm.get('clean_race_count')/sm.get('race_count')) if sm.get('race_count') else None
    racecraft=clamp(.55*advancement+.30*clamp(top5*100)+.15*clamp((clean_rate or 0)*100)); results=clamp(.65*clamp(top5*100)+.35*clamp(win*500)); overall=clamp(.30*qualifying+.35*race_performance+.20*racecraft+.15*results) if qualifying is not None and race_performance is not None else None
    return {"qualifying":qualifying,"race_performance":race_performance,"racecraft":racecraft,"results":results,"overall":overall,"method":"provisional_absolute"}

def load_indices(career):
    if CALIBRATION_PATH.exists():
        try:
            c=json.loads(CALIBRATION_PATH.read_text(encoding="utf-8")); adj=c.get("user_percentiles_dr_adjusted") or c.get("user_percentiles",{}); glob=c.get("user_percentiles_global") or c.get("user_percentiles",{}); required=("qualifying","race_performance","racecraft","results","overall")
            if all(isinstance(adj.get(k),(int,float)) for k in required):return {"method":"empirical_percentile_dr_adjusted","adjusted":adj,"global":glob,"sample_size":c.get("valid_profiles"),"reference_population":c.get("reference_population"),"calibrated_at":c.get("captured_at"),"dr_adjustment":c.get("dr_adjustment")}
        except Exception:pass
    return provisional_indices(career)

def main():
    rating=json.loads(RATING_PATH.read_text()); career=json.loads(CAREER_PATH.read_text()); idx=load_indices(career); empirical=idx.get("method")=="empirical_percentile_dr_adjusted"
    lines=["# GT7 Sport Career Report","",f"PSN: **{rating['psn_id']}**  ",f"Updated: {career['captured_at']}  ",f"DR: **{rating['dr_label']}** — {rating['dr_points']} points — {rating['dr_percentage']}% toward next DR  ",f"SR: **{rating['sportsmanship_rating']}**",""]
    if empirical:
        a=idx['adjusted']; g=idx.get('global',{}); meta=idx.get('dr_adjustment') or {}; effective=meta.get('effective_peer_count'); peer_text=f"{effective:.0f} effective" if isinstance(effective,(int,float)) else str(meta.get('peer_count','n/a'))
        display=(("Qualifying Pace","qualifying"),("Finishing Performance","race_performance"),("Position Conversion","racecraft"),("Results","results"),("Career Rating","overall"))
        lines += ["## Competitive ratings","","| Dimension | Rating | Assessment | Statistical context |","|---|---:|---|---|"]
        for name,key in display:
            p=a[key]; grade=competitive_grade(p); lines.append(f"| **{name}** | **{grade:.1f}/10** | {grade_label(grade)} | {metric_explanation(name,p)} |")
        lines += ["","> Ratings are the presentation layer; the underlying cross-DR percentiles remain the statistical benchmark. The nonlinear 0–10 scale is anchored at 5.0 for the median (P50), ~7.0 for P75, ~8.3 for P90, ~8.9 for P95 and ~9.6 for P99.","",f"> Cross-DR peer reference: {peer_text}. Calibration sample: {idx.get('sample_size')} valid profiles. Global percentiles remain available as population context.",""]
    else:
        lines += ["## Performance indices (provisional)","",f"Qualifying: **{idx['qualifying']:.1f}/100**  ",f"Race Performance: **{idx['race_performance']:.1f}/100**  ",f"Racecraft: **{idx['racecraft']:.1f}/100**  ",f"Results: **{idx['results']:.1f}/100**  ",f"Overall Career: **{idx['overall']:.1f}/100**","","> Provisional absolute indices. Population calibration has not completed yet.",""]
    lines += ["## Sport career","","| Category | Races | Wins | Top 5 | Poles | Avg grid | Avg finish | Avg positions gained | Win rate | Top-5 rate | Pole rate |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in career.get('sport_types',[]):lines.append(f"| {sport_label(r['sport_type'])} | {r['races']} | {r['wins']} | {r['top5']} | {r['poles']} | {num(r['average_grid'])} | {num(r['average_finish'])} | {num(r['positions_gained_avg'])} | {pct(r['win_rate'])} | {pct(r['top5_rate'])} | {pct(r['pole_rate'])} |")
    sm=career.get('sports_mode',{}); qp=career.get('qualifying_performance',{})
    lines += ["","## Sports Mode counters","",f"Races: **{sm.get('race_count')}** · Wins: **{sm.get('win_count')}** · Poles: **{sm.get('pole_position_count')}** · Fastest laps: **{sm.get('fastest_lap_count')}** · Clean races: **{sm.get('clean_race_count')}**","","## Qualifying performance","",f"Best rank: **{qp.get('best_rank')}** · Median rank: **{qp.get('median_rank')}** · Average rank: **{qp.get('average_rank')}** · Worst rank: **{qp.get('worst_rank')}** · Rank standard deviation: **{qp.get('rank_stddev')}**","","## Performance trend",""]
    for days in (7,30,90):
        tr=load_performance_trend(days); lines.append(f"### Last {days} days")
        if tr.get('status')=='insufficient_data':lines.append(f"Insufficient calibrated history for a reliable trend ({tr.get('snapshot_count',0)} snapshot(s)).")
        else:
            note="" if tr['status']=='ok' else f" · partial coverage: {tr.get('covered_days',0)} days"; lines.append(f"Overall percentile: {tr['overall_start']:.1f} → {tr['overall_end']:.1f} ({signed(tr['overall_change'],1)}) · DR points {signed(tr['dr_points_change'],0)} · {tr['snapshot_count']} snapshots{note}"); lines.append(f"- Qualifying {signed(tr['qualifying_change'],1)} · Finishing Performance {signed(tr['race_performance_change'],1)} · Position Conversion {signed(tr['racecraft_change'],1)} · Results {signed(tr['results_change'],1)}")
        lines.append("")
    lines += ["## Activity trends",""]
    for days in (7,30,90):
        dr=load_dr_trend(days); lines.append(f"### Last {days} days")
        if dr.get('status')=='insufficient_data':lines.append("DR: insufficient history for a reliable trend.")
        else:
            note="" if dr['status']=='ok' else f" · partial coverage: {dr.get('covered_days',0)} days"; lines.append(f"DR: {dr['from_label']} → {dr['to_label']} · points {signed(dr['dr_points_change'],0)} · progress {signed(dr['dr_percentage_change'],1)} pp{note}")
        for tr in load_period_trends(days):
            label=sport_label(tr['sport_type'])
            if tr.get('status')=='insufficient_data':lines.append(f"- {label}: insufficient history."); continue
            note="" if tr['status']=='ok' else f" · partial coverage: {tr.get('covered_days',0)} days"; lines.append(f"- {label}: {tr['races']} races · {tr['wins']} wins · {tr['top5']} Top 5 · {tr['poles']} poles · win rate {pct(tr['period_win_rate'])} · Top-5 rate {pct(tr['period_top5_rate'])} · avg grid change {signed(tr['avg_grid_change'])} · avg finish change {signed(tr['avg_finish_change'])}{note}")
        lines.append("")
    lines += ["## Interpretation",""]
    for r in career.get('sport_types',[]):
        g=r.get('positions_gained_avg')
        if g is not None:lines.append(f"- {sport_label(r['sport_type'])}: on average {'gains' if g>=0 else 'loses'} **{abs(g):.2f} positions per race** from qualifying/grid position to finish.")
    lines += ["- Cross-DR percentiles remain the underlying longitudinal benchmark because matchmaking difficulty rises with DR.","- The 0–10 ratings are a nonlinear presentation layer designed for intuitive interpretation.","- Position Conversion is the presentation name for the historical racecraft field; the database field is retained for compatibility.","- Performance trends use stored snapshots and never recalculate historical scores with a newer benchmark."]
    REPORT_PATH.parent.mkdir(parents=True,exist_ok=True); REPORT_PATH.write_text("\n".join(lines)+"\n"); print(f"Career report written to {REPORT_PATH}")

if __name__=='__main__':main()
