from __future__ import annotations
import json,time
from collections import Counter
from datetime import datetime,timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import calibrate_population as base
RETRY_STATUS={429,500,502,503,504}; MAX_EVENT_PAGES=3; MAX_EVENT_ATTEMPTS=3; MIN_VALID_SAMPLE=100

def candidate_events(session):
    urls=[]; seen=set()
    for page in range(1,MAX_EVENT_PAGES+1):
        url=base.GTSH_DAILY if page==1 else f"{base.GTSH_DAILY}?page={page}&q="; r=session.get(url,timeout=30); r.raise_for_status(); soup=BeautifulSoup(r.text,"html.parser"); scored=[]
        for link in soup.select('a[href*="/daily/leaderboard?event="], a[href*="/daily/leaderboard/?event="]'):
            href=link.get("href"); full=urljoin(base.GTSH_DAILY,href) if href else None
            if not full or full in seen:continue
            text=link.parent.get_text(" ",strip=True) if link.parent else ""; priority=0 if "Daily Race C" in text else 1 if "Daily Race B" in text else 2; scored.append((priority,full)); seen.add(full)
        urls.extend(full for _,full in sorted(scored))
    return urls
def try_sample_event(session,event_url):
    last=None
    for attempt in range(1,MAX_EVENT_ATTEMPTS+1):
        try:print(f"Trying leaderboard attempt {attempt}: {event_url}"); return base.systematic_psns(session,event_url,base.TARGET_SAMPLE)
        except requests.HTTPError as exc:
            last=exc; status=exc.response.status_code if exc.response is not None else None
            if status not in RETRY_STATUS:break
            time.sleep(2**attempt)
        except Exception as exc:last=exc; break
    raise RuntimeError(f"Leaderboard unusable: {event_url}: {last}")
def main():
    session=requests.Session(); session.headers.update(base.HEADERS); events=candidate_events(session)
    chosen=None; psns=None; total=None; leaderboard_failures=[]
    for event in events:
        try:
            psns,total=try_sample_event(session,event)
            if psns and total:chosen=event; print(f"Selected calibration leaderboard: {event}"); break
        except Exception as exc:leaderboard_failures.append(str(exc)); print(str(exc))
    if not chosen:raise RuntimeError("No accessible leaderboard found")
    print(f"Leaderboard participants: {total:,}; PSNs selected: {len(psns)}")
    sample=[]; request_failures=0; rejection_counts=Counter(); rejection_examples={}
    for i,psn in enumerate(psns,1):
        try:
            row,reasons=base.fetch_profile_diagnostic(session,psn)
            if row:sample.append(row)
            else:
                for reason in reasons:
                    rejection_counts[reason]+=1; rejection_examples.setdefault(reason,[])
                    if len(rejection_examples[reason])<5:rejection_examples[reason].append(psn)
        except Exception as exc:request_failures+=1; rejection_counts["request_exception"]+=1; print(f"Profile failed {psn}: {exc}")
        if i%25==0 or i==len(psns):print(f"Profiles {i}/{len(psns)}; valid {len(sample)}; rejected {i-len(sample)-request_failures}; request failures {request_failures}")
        time.sleep(base.DELAY)
    yield_rate=len(sample)/len(psns) if psns else 0
    print(f"Valid profile yield: {yield_rate:.1%} ({len(sample)}/{len(psns)})"); print("Rejection reasons:",dict(rejection_counts))
    if len(sample)<MIN_VALID_SAMPLE:raise RuntimeError(f"Calibration sample too small: {len(sample)} valid profiles; minimum required is {MIN_VALID_SAMPLE}")
    scores=base.metric_percentiles(base.user_daily(),sample)
    output={"captured_at":datetime.now(timezone.utc).isoformat(),"method":"validated_systematic_uniform_sample_with_multi_event_fallback","reference_population":"active/recent Daily Race leaderboard participants with at least 20 career Daily Races and physically valid career metrics","event_url":chosen,"leaderboard_total":total,"target_sample":base.TARGET_SAMPLE,"selected_psns":len(psns),"valid_profiles":len(sample),"valid_profile_yield":yield_rate,"rejected_profiles":len(psns)-len(sample)-request_failures,"profile_failures":request_failures,"minimum_valid_sample":MIN_VALID_SAMPLE,"minimum_races":base.MIN_RACES,"validation":{"average_grid_range":[1,20],"average_finish_range":[1,20],"wins_lte_races":True,"top5_lte_races":True,"wins_lte_top5":True},"rejection_reasons":dict(rejection_counts),"rejection_examples":rejection_examples,"leaderboard_failures_before_success":leaderboard_failures,"user_percentiles":scores,"sample_summary":{key:{"min":min(r[key] for r in sample),"median":sorted(r[key] for r in sample)[len(sample)//2],"max":max(r[key] for r in sample)} for key in ("average_grid","average_finish","positions_gained_avg","win_rate","top5_rate")}}
    base.OUT_PATH.parent.mkdir(parents=True,exist_ok=True); base.OUT_PATH.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n"); print(json.dumps(scores,indent=2))
if __name__=="__main__":main()
