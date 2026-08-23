from __future__ import annotations

import base64, json, math, os, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
import requests
from bs4 import BeautifulSoup

GTSH_DAILY="https://gtsh-rank.com/daily/"; PROFILE_BASE="https://gtsh-rank.com/profile/?id="; OUT_PATH=Path("data/population_calibration.json")
MY_PSN=os.getenv("GT7_PSN_ID","crazy_rooster74"); TARGET_SAMPLE=int(os.getenv("GT7_CALIBRATION_SAMPLE","160")); MIN_RACES=int(os.getenv("GT7_CALIBRATION_MIN_RACES","20")); MAX_OBSERVED_DAILY_GRID=16; PAGE_SIZE=100; DELAY=float(os.getenv("GT7_CALIBRATION_DELAY","0.08")); HEADERS={"User-Agent":"Mozilla/5.0 (GT7 Career Population Calibration)"}
RACECRAFT_GRID_WINDOW=1.5; RACECRAFT_MIN_PEERS=20

def extract_json_variable(html,name):
    for marker in (f"const {name} = ",f"let {name} = ",f"var {name} = "):
        idx=html.find(marker)
        if idx>=0:
            try:return json.JSONDecoder().raw_decode(html[idx+len(marker):].lstrip())[0]
            except Exception:pass
    return None

def canonical(url):
    p=urlparse(url); path=p.path.rstrip("/"); path += "/" if path.endswith("/daily/leaderboard") else ""; return urlunparse((p.scheme,p.netloc,path,p.params,p.query,p.fragment))
def page_url(event_url,offset,limit=PAGE_SIZE):
    p=urlparse(canonical(event_url)); q=parse_qs(p.query,keep_blank_values=True); q.update(page_data=["1"],offset=[str(offset)],limit=[str(limit)]); return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),p.fragment))
def discover_current_event(session):
    r=session.get(GTSH_DAILY,timeout=30); r.raise_for_status(); soup=BeautifulSoup(r.text,"html.parser"); links=soup.select('a[href*="/daily/leaderboard?event="], a[href*="/daily/leaderboard/?event="]')
    for link in links:
        if "Daily Race C" in (link.parent.get_text(" ",strip=True) if link.parent else ""):return urljoin(GTSH_DAILY,link.get("href"))
    if links:return urljoin(GTSH_DAILY,links[0].get("href"))
    raise RuntimeError("No current Daily Race leaderboard found")
def fetch_page_payload(session,event_url,offset):
    r=session.get(page_url(event_url,offset),headers={**HEADERS,"Accept":"application/json"},timeout=60); r.raise_for_status(); data=r.json()
    if not isinstance(data,dict) or not isinstance(data.get("board"),list):raise RuntimeError(f"Paged leaderboard response invalid at offset {offset}")
    return data
def initial_board(session,event_url):
    r=session.get(canonical(event_url),timeout=60); r.raise_for_status(); initial=extract_json_variable(r.text,"initialServerPage")
    if isinstance(initial,dict) and isinstance(initial.get("board"),list) and int(initial.get("total",0) or 0)>0:return initial["board"],int(initial["total"])
    ranking=extract_json_variable(r.text,"initialRanking")
    if isinstance(ranking,list) and ranking:
        try:
            p=fetch_page_payload(session,event_url,0); total=int(p.get("total",0) or 0)
            if total>0: print("Leaderboard bootstrap: initialRanking + paged metadata"); return p["board"],total
        except Exception: pass
        return ranking,len(ranking)
    p=fetch_page_payload(session,event_url,0); total=int(p.get("total",0) or 0)
    if total>0:return p["board"],total
    raise RuntimeError("Leaderboard total is unavailable")
def fetch_board_page(session,event_url,offset):return fetch_page_payload(session,event_url,offset)["board"]
def online_id(driver):
    user=driver.get("user",{}); v=user.get("np_online_id") if isinstance(user,dict) else None; return v.strip() if isinstance(v,str) and v.strip() else None
def systematic_psns(session,event_url,target):
    first,total=initial_board(session,event_url); ranks=[max(1,min(total,round((i+.5)*total/target))) for i in range(target)]; by={}
    for rank in ranks:by.setdefault(((rank-1)//PAGE_SIZE)*PAGE_SIZE,[]).append(rank)
    out=[]; seen=set(); firstmap={int(d.get("display_rank",0)):d for d in first if d.get("display_rank")}
    for offset,wanted in sorted(by.items()):
        boardmap=firstmap if offset==0 else {int(d.get("display_rank",0)):d for d in fetch_board_page(session,event_url,offset) if d.get("display_rank")}
        for rank in wanted:
            psn=online_id(boardmap.get(rank,{}))
            if psn and psn.lower() not in seen and psn.lower()!=MY_PSN.lower():seen.add(psn.lower()); out.append(psn)
        if offset:time.sleep(DELAY)
    return out,total
def xor_decrypt(data,key):
    kb=key.encode(); return bytes(b^kb[i%len(kb)] for i,b in enumerate(data)).decode()

def validate_metrics(races,wins,top5,avg_grid,avg_finish):
    reasons=[]; vals=(races,wins,top5,avg_grid,avg_finish)
    if not all(isinstance(x,(int,float)) and math.isfinite(float(x)) for x in vals):reasons.append("non_numeric")
    if reasons:return reasons
    if races<MIN_RACES:reasons.append("below_min_races")
    if wins<0 or wins>races:reasons.append("wins_out_of_range")
    if top5<0 or top5>races:reasons.append("top5_out_of_range")
    if wins>top5:reasons.append("wins_gt_top5")
    if not 1.0<=avg_grid<=MAX_OBSERVED_DAILY_GRID:reasons.append("average_grid_out_of_range")
    if not 1.0<=avg_finish<=MAX_OBSERVED_DAILY_GRID:reasons.append("average_finish_out_of_range")
    return reasons

def fetch_profile_diagnostic(session,psn):
    url=PROFILE_BASE+psn; page=session.get(url,timeout=30); page.raise_for_status(); soup=BeautifulSoup(page.text,"html.parser"); body=soup.find("body"); key=body.get("header") if body else None
    if not key:return None,["missing_decryption_key"]
    response=session.post(url,headers={"Content-Type":"application/x-www-form-urlencoded","Referer":url,"Origin":"https://gtsh-rank.com","Accept":"application/json,text/plain,*/*"},data={"psnid":psn},timeout=60); response.raise_for_status(); wrapper=response.json(); enc=wrapper.get("data") if isinstance(wrapper,dict) else None
    if not isinstance(enc,str):return None,["missing_encrypted_payload"]
    payload=json.loads(xor_decrypt(base64.b64decode(enc),key)); rows=payload.get("sport",{}).get("result",[]) if isinstance(payload,dict) else []; daily=next((r for r in rows if isinstance(r,dict) and r.get("type")==1),None)
    if not daily:return None,["missing_daily_stats"]
    races=daily.get("race"); wins=daily.get("win") or 0; top5=daily.get("top5") or 0; avg_grid=daily.get("average_qualify_rank"); avg_finish=daily.get("average_rank"); reasons=validate_metrics(races,wins,top5,avg_grid,avg_finish)
    if reasons:return None,reasons
    row={"psn_id":psn,"races":int(races),"wins":int(wins),"top5":int(top5),"average_grid":float(avg_grid),"average_finish":float(avg_finish)}; row["positions_gained_avg"]=row["average_grid"]-row["average_finish"]; row["win_rate"]=wins/races; row["top5_rate"]=top5/races; return row,[]
def fetch_profile(session,psn):return fetch_profile_diagnostic(session,psn)[0]
def percentile(value,values,higher_better):
    clean=sorted(v for v in values if isinstance(v,(int,float)) and math.isfinite(v)); return None if not clean else 100*sum((v<=value) if higher_better else (v>=value) for v in clean)/len(clean)

def conditioned_racecraft(user,sample):
    user_grid=float(user["average_grid"]); gain=float(user["positions_gained_avg"]); window=RACECRAFT_GRID_WINDOW
    peers=[r for r in sample if abs(float(r["average_grid"])-user_grid)<=window]
    # For small pilot samples, widen symmetrically until the comparison contains
    # enough similarly-qualifying drivers to avoid an unstable percentile.
    while len(peers)<RACECRAFT_MIN_PEERS and window<5.0:
        window+=0.5; peers=[r for r in sample if abs(float(r["average_grid"])-user_grid)<=window]
    score=percentile(gain,[float(r["positions_gained_avg"]) for r in peers],True) if peers else None
    return score,{"peer_count":len(peers),"grid_window":window,"grid_min":user_grid-window,"grid_max":user_grid+window,"user_average_grid":user_grid,"user_positions_gained_avg":gain}

def user_daily():
    c=json.loads(Path("data/latest_career.json").read_text()); return next(r for r in c.get("sport_types",[]) if r.get("sport_type")==1)
def metric_percentiles(user,sample):
    specs={"average_grid":False,"average_finish":False,"positions_gained_avg":True,"win_rate":True,"top5_rate":True}; out={k:percentile(float(user[k]),[float(r[k]) for r in sample],h) for k,h in specs.items()}; out["results"]=(out["win_rate"]+out["top5_rate"])/2; out["qualifying"]=out["average_grid"]; out["race_performance"]=out["average_finish"]
    conditioned,meta=conditioned_racecraft(user,sample); out["racecraft_unconditioned"]=out["positions_gained_avg"]; out["racecraft"]=conditioned; out["racecraft_conditioning"]=meta
    out["overall"]=.30*out["qualifying"]+.35*out["race_performance"]+.20*out["racecraft"]+.15*out["results"]; return out
