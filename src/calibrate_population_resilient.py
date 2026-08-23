from __future__ import annotations

import base64
import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import calibrate_population as base

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_EVENT_PAGES = 3
MAX_EVENT_ATTEMPTS = 3
EVENTS_PER_RUN = int(os.getenv("GT7_CALIBRATION_EVENTS_PER_RUN", "3"))
TARGET_VALID_REFERENCE = int(os.getenv("GT7_CALIBRATION_TARGET_VALID", "2000"))
MIN_VALID_TO_PUBLISH = int(os.getenv("GT7_CALIBRATION_MIN_VALID", "100"))
CACHE_PATH = Path("data/population_profiles.json")
RATING_PATH = Path("data/latest_rating.json")
DR_WEIGHT_SCALE = float(os.getenv("GT7_DR_WEIGHT_SCALE", "5000"))
RACECRAFT_GRID_WINDOW = 1.5
RACECRAFT_MIN_EFFECTIVE_PEERS = 20


def candidate_events(session: requests.Session) -> list[str]:
    urls, seen = [], set()
    for page in range(1, MAX_EVENT_PAGES + 1):
        url = base.GTSH_DAILY if page == 1 else f"{base.GTSH_DAILY}?page={page}&q="
        r = session.get(url, timeout=30); r.raise_for_status(); soup = BeautifulSoup(r.text, "html.parser")
        scored = []
        for link in soup.select('a[href*="/daily/leaderboard?event="], a[href*="/daily/leaderboard/?event="]'):
            href=link.get("href"); full=urljoin(base.GTSH_DAILY,href) if href else None
            if not full or full in seen: continue
            text=link.parent.get_text(" ",strip=True) if link.parent else ""; priority=0 if "Daily Race C" in text else 1 if "Daily Race B" in text else 2
            scored.append((priority,full)); seen.add(full)
        urls.extend(full for _,full in sorted(scored))
    return urls


def try_sample_event(session,event_url,target):
    last=None
    for attempt in range(1,MAX_EVENT_ATTEMPTS+1):
        try: print(f"Trying leaderboard attempt {attempt}: {event_url}"); return base.systematic_psns(session,event_url,target)
        except requests.HTTPError as exc:
            last=exc; status=exc.response.status_code if exc.response is not None else None
            if status not in RETRY_STATUS: break
            time.sleep(2**attempt)
        except Exception as exc: last=exc; break
    raise RuntimeError(f"Leaderboard unusable: {event_url}: {last}")


def load_cache():
    if not CACHE_PATH.exists(): return {"profiles":{},"unavailable":{},"filtered":{}}
    try:
        obj=json.loads(CACHE_PATH.read_text(encoding="utf-8")); obj.setdefault("profiles",{}); obj.setdefault("unavailable",{}); obj.setdefault("filtered",{}); return obj
    except Exception as exc:
        print(f"Population cache unreadable; starting fresh: {exc}"); return {"profiles":{},"unavailable":{},"filtered":{}}


def save_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True,exist_ok=True); payload={"updated_at":datetime.now(timezone.utc).isoformat(),"target_valid_profiles":TARGET_VALID_REFERENCE,**cache}; CACHE_PATH.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def norm(psn): return psn.casefold()
def record_status(bucket,psn,reason=None): bucket[norm(psn)]={"psn_id":psn,"reason":reason,"last_attempt_at":datetime.now(timezone.utc).isoformat()}


def fetch_profile_with_rating(session:requests.Session,psn:str):
    url=base.PROFILE_BASE+psn; page=session.get(url,timeout=30); page.raise_for_status(); soup=BeautifulSoup(page.text,"html.parser"); body=soup.find("body"); key=body.get("header") if body else None
    if not key:return None,["missing_decryption_key"]
    response=session.post(url,headers={"Content-Type":"application/x-www-form-urlencoded","Referer":url,"Origin":"https://gtsh-rank.com","Accept":"application/json,text/plain,*/*"},data={"psnid":psn},timeout=60); response.raise_for_status(); wrapper=response.json(); enc=wrapper.get("data") if isinstance(wrapper,dict) else None
    if not isinstance(enc,str):return None,["missing_encrypted_payload"]
    payload=json.loads(base.xor_decrypt(base64.b64decode(enc),key)); rows=payload.get("sport",{}).get("result",[]) if isinstance(payload,dict) else []; daily=next((r for r in rows if isinstance(r,dict) and r.get("type")==1),None)
    if not daily:return None,["missing_daily_stats"]
    races=daily.get("race"); wins=daily.get("win") or 0; top5=daily.get("top5") or 0; avg_grid=daily.get("average_qualify_rank"); avg_finish=daily.get("average_rank"); reasons=base.validate_metrics(races,wins,top5,avg_grid,avg_finish)
    if reasons:return None,reasons
    user=payload.get("monthly_stats",{}).get("result",{}).get("user",{}); dr_code=user.get("driver_rating") if isinstance(user,dict) else None; dr_points=user.get("dr_points") if isinstance(user,dict) else None
    if not isinstance(dr_code,(int,float)):return None,["missing_driver_rating"]
    row={"psn_id":psn,"races":int(races),"wins":int(wins),"top5":int(top5),"average_grid":float(avg_grid),"average_finish":float(avg_finish),"driver_rating":int(dr_code),"dr_label":user.get("dr_level"),"dr_points":float(dr_points) if isinstance(dr_points,(int,float)) else None,"dr_percentage":user.get("dr_percentage")}; row["positions_gained_avg"]=row["average_grid"]-row["average_finish"]; row["win_rate"]=wins/races; row["top5_rate"]=top5/races
    return row,[]


def profile_with_retry(session,psn):
    last_exc=None
    for attempt in range(1,4):
        try:return fetch_profile_with_rating(session,psn)
        except requests.HTTPError as exc:
            last_exc=exc; status=exc.response.status_code if exc.response is not None else None
            if status not in RETRY_STATUS:raise
            time.sleep(2**attempt)
        except requests.RequestException as exc:last_exc=exc; time.sleep(2**attempt)
    if last_exc:raise last_exc
    return None,["unknown_profile_failure"]


def sample_summary(sample):
    return {key:{"min":min(r[key] for r in sample),"median":sorted(r[key] for r in sample)[len(sample)//2],"max":max(r[key] for r in sample)} for key in ("average_grid","average_finish","positions_gained_avg","win_rate","top5_rate")}


def dr_weight(row,user_pts):
    pts=row.get("dr_points")
    if not isinstance(pts,(int,float)) or not isinstance(user_pts,(int,float)):return 0.0
    # DR points are themselves continuous across grade boundaries (C/B/A/A+).
    # Weight every rated profile by absolute point distance, with no letter filter.
    # At 5k points distance a peer contributes ~36.8%; at 10k ~13.5%.
    return math.exp(-abs(float(pts)-float(user_pts))/DR_WEIGHT_SCALE)


def weighted_percentile(value,rows,key,higher_better,weight_fn):
    numerator=denominator=weight_sq=0.0; count=0
    for row in rows:
        v=row.get(key); w=weight_fn(row)
        if not isinstance(v,(int,float)) or not math.isfinite(float(v)) or w<=0:continue
        denominator+=w; weight_sq+=w*w; count+=1
        if (float(v)<=float(value)) if higher_better else (float(v)>=float(value)): numerator+=w
    score=100*numerator/denominator if denominator else None
    effective=(denominator*denominator/weight_sq) if weight_sq else 0.0
    return score,{"raw_peer_count":count,"weight_sum":denominator,"effective_peer_count":effective}


def weighted_racecraft(user,rows,weight_fn):
    user_grid=float(user["average_grid"]); gain=float(user["positions_gained_avg"]); window=RACECRAFT_GRID_WINDOW
    while True:
        peers=[r for r in rows if isinstance(r.get("average_grid"),(int,float)) and abs(float(r["average_grid"])-user_grid)<=window]
        score,meta=weighted_percentile(gain,peers,"positions_gained_avg",True,weight_fn)
        if meta["effective_peer_count"]>=RACECRAFT_MIN_EFFECTIVE_PEERS or window>=5.0:break
        window+=0.5
    meta.update({"grid_window":window,"grid_min":user_grid-window,"grid_max":user_grid+window,"user_average_grid":user_grid,"user_positions_gained_avg":gain})
    return score,meta


def adjusted_percentiles(user,sample,rating):
    user_dr=int(rating["driver_rating"]); user_pts=rating.get("dr_points")
    rated=[r for r in sample if isinstance(r.get("dr_points"),(int,float))]
    weight_fn=lambda r: dr_weight(r,user_pts)
    specs={"average_grid":False,"average_finish":False,"positions_gained_avg":True,"win_rate":True,"top5_rate":True}; scores={}; metric_meta={}
    for key,higher in specs.items(): scores[key],metric_meta[key]=weighted_percentile(float(user[key]),rated,key,higher,weight_fn)
    scores["results"]=(scores["win_rate"]+scores["top5_rate"])/2; scores["qualifying"]=scores["average_grid"]; scores["race_performance"]=scores["average_finish"]; scores["racecraft_unconditioned"]=scores["positions_gained_avg"]
    rc,rc_meta=weighted_racecraft(user,rated,weight_fn); scores["racecraft"]=rc; scores["racecraft_conditioning"]=rc_meta; scores["overall"]=.30*scores["qualifying"]+.35*scores["race_performance"]+.20*scores["racecraft"]+.15*scores["results"]
    weights=[weight_fn(r) for r in rated if weight_fn(r)>0]; sw=sum(weights); sw2=sum(w*w for w in weights); effective=sw*sw/sw2 if sw2 else 0
    pts=[r["dr_points"] for r in rated]
    by_dr={}
    for r in rated:
        label=r.get("dr_label") or str(r.get("driver_rating")); w=weight_fn(r)
        if w<=0:continue
        bucket=by_dr.setdefault(str(label),{"raw_peer_count":0,"weight_sum":0.0})
        bucket["raw_peer_count"]+=1; bucket["weight_sum"]+=w
    meta={"method":"cross_dr_continuous_exponential_weighting","driver_rating":user_dr,"user_dr_points":user_pts,"raw_peer_count":len(rated),"effective_peer_count":effective,"dr_weight_scale_points":DR_WEIGHT_SCALE,"dr_points_min":min(pts) if pts else None,"dr_points_max":max(pts) if pts else None,"weight_contribution_by_dr":by_dr,"racecraft_raw_peer_count":rc_meta.get("raw_peer_count"),"racecraft_effective_peer_count":rc_meta.get("effective_peer_count"),"racecraft_grid_window":rc_meta.get("grid_window"),"metric_effective_peers":{k:v["effective_peer_count"] for k,v in metric_meta.items()}}
    return scores,meta


def main():
    session=requests.Session(); session.headers.update(base.HEADERS); cache=load_cache(); rating=json.loads(RATING_PATH.read_text(encoding="utf-8")); existing=len(cache["profiles"]); print(f"Cumulative reference before run: {existing}/{TARGET_VALID_REFERENCE} valid profiles")
    incomplete=[r.get("psn_id") for r in cache["profiles"].values() if not isinstance(r.get("driver_rating"),(int,float)) and r.get("psn_id")]
    if incomplete:print(f"Refreshing {len(incomplete)} cached profiles to add DR metadata")
    for i,psn in enumerate(incomplete,1):
        try:
            row,reasons=profile_with_retry(session,psn)
            if row:row["collected_at"]=datetime.now(timezone.utc).isoformat(); cache["profiles"][norm(psn)]=row
            elif reasons:cache["profiles"].pop(norm(psn),None); record_status(cache["unavailable"],psn,",".join(reasons))
        except Exception as exc:print(f"DR refresh failed {psn}: {exc}")
        if i%50==0:print(f"DR metadata refresh {i}/{len(incomplete)}")
        time.sleep(base.DELAY)
    selected_events=[]; candidate_psns=[]; candidate_seen=set(); leaderboard_failures=[]
    if len(cache["profiles"])<TARGET_VALID_REFERENCE:
        events=candidate_events(session); per_event=max(100,math.ceil(base.TARGET_SAMPLE/max(1,EVENTS_PER_RUN)))
        for event_url in events:
            if len(selected_events)>=EVENTS_PER_RUN:break
            try:
                psns,total=try_sample_event(session,event_url,per_event); selected_events.append({"url":event_url,"participants":total,"selected_psns":len(psns)})
                for psn in psns:
                    key=norm(psn)
                    if key in candidate_seen or key in cache["profiles"]:continue
                    candidate_seen.add(key); candidate_psns.append(psn)
            except Exception as exc:leaderboard_failures.append(str(exc)); print(str(exc))
    candidate_psns=candidate_psns[:base.TARGET_SAMPLE]; print(f"Events used: {len(selected_events)}; unique uncached PSNs this run: {len(candidate_psns)}")
    run_valid=unavailable=request_failures=0; rejection_counts=Counter(); rejection_examples={}
    for i,psn in enumerate(candidate_psns,1):
        try:
            row,reasons=profile_with_retry(session,psn)
            if row:
                row["collected_at"]=datetime.now(timezone.utc).isoformat(); cache["profiles"][norm(psn)]=row; cache["unavailable"].pop(norm(psn),None); cache["filtered"].pop(norm(psn),None); run_valid+=1
            else:
                reasons=reasons or ["unknown"]
                if any(r in {"missing_encrypted_payload","missing_decryption_key","missing_daily_stats"} for r in reasons):unavailable+=1; record_status(cache["unavailable"],psn,",".join(reasons))
                else:
                    for reason in reasons:
                        rejection_counts[reason]+=1; rejection_examples.setdefault(reason,[])
                        if len(rejection_examples[reason])<5:rejection_examples[reason].append(psn)
                    record_status(cache["filtered"],psn,",".join(reasons))
        except Exception as exc:request_failures+=1; print(f"Profile failed {psn}: {exc}")
        if i%50==0 or i==len(candidate_psns):print(f"Profiles {i}/{len(candidate_psns)}; new valid {run_valid}; unavailable {unavailable}; quality-filtered {sum(rejection_counts.values())}; request failures {request_failures}")
        time.sleep(base.DELAY)
    save_cache(cache); sample=[r for r in cache["profiles"].values() if isinstance(r.get("driver_rating"),(int,float))]; cumulative=len(sample)
    if cumulative<MIN_VALID_TO_PUBLISH:raise RuntimeError(f"Cumulative DR-aware calibration sample too small: {cumulative}")
    user=base.user_daily(); global_scores=base.metric_percentiles(user,sample); adjusted,adjust_meta=adjusted_percentiles(user,sample,rating)
    output={"captured_at":datetime.now(timezone.utc).isoformat(),"method":"cumulative_multi_event_cross_dr_adjusted_reference","reference_population":"active/recent Daily Race leaderboard participants with at least 20 career Daily Races and physically valid career metrics","target_valid_profiles":TARGET_VALID_REFERENCE,"valid_profiles":cumulative,"target_reached":cumulative>=TARGET_VALID_REFERENCE,"progress_to_target":cumulative/TARGET_VALID_REFERENCE,"new_valid_profiles_this_run":run_valid,"events_used":selected_events,"unique_uncached_psns_attempted":len(candidate_psns),"unavailable_profiles_this_run":unavailable,"quality_filtered_this_run":sum(rejection_counts.values()),"profile_request_failures_this_run":request_failures,"minimum_races":base.MIN_RACES,"validation":{"average_grid_range":[1,base.MAX_OBSERVED_DAILY_GRID],"average_finish_range":[1,base.MAX_OBSERVED_DAILY_GRID],"wins_lte_races":True,"top5_lte_races":True,"wins_lte_top5":True},"rejection_reasons_this_run":dict(rejection_counts),"rejection_examples_this_run":rejection_examples,"leaderboard_failures_before_success":leaderboard_failures,"user_rating":{"driver_rating":rating.get("driver_rating"),"dr_label":rating.get("dr_label"),"dr_points":rating.get("dr_points"),"dr_percentage":rating.get("dr_percentage")},"user_percentiles_global":global_scores,"user_percentiles_dr_adjusted":adjusted,"dr_adjustment":adjust_meta,"user_percentiles":adjusted or global_scores,"sample_summary":sample_summary(sample)}
    base.OUT_PATH.parent.mkdir(parents=True,exist_ok=True); base.OUT_PATH.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(f"Cumulative DR-aware reference: {cumulative}/{TARGET_VALID_REFERENCE} ({cumulative/TARGET_VALID_REFERENCE:.1%})"); print("DR adjustment:",json.dumps(adjust_meta,indent=2)); print(json.dumps(adjusted,indent=2))

if __name__=="__main__":main()
