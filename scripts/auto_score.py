from __future__ import annotations
import os, sys, time
from datetime import date, timedelta
from pathlib import Path
import yfinance as yf
from supabase import create_client

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scoring_engine import compute_pabrai_auto_score

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
BATCH_SIZE = int(os.environ.get("BATCH_SIZE") or "30")
REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS") or "30")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

def get_active_universe():
    r=(supabase.table("investment_universe")
       .select("""company_id,universe,rank,companies(id,ticker,company_name,market,country)""")
       .eq("is_active", True).limit(1000).execute())
    return r.data or []

def get_current_scores():
    r=(supabase.table("framework_scores").select("company_id,as_of,status")
       .eq("framework_id","PABRAI_AUTO").limit(1000).execute())
    return {x["company_id"]:x for x in (r.data or [])}

def yahoo_symbol(company, universe):
    t=str(company["ticker"]).strip()
    return f"{t}.KS" if universe=="KOSPI_TOP200" else t.replace(".","-")

def fetch_info(symbol, attempts=3):
    err=None
    for i in range(attempts):
        try:
            info=yf.Ticker(symbol).get_info()
            if isinstance(info, dict) and info:
                return info
        except Exception as e:
            err=e
        time.sleep(2**i)
    if err: raise err
    raise RuntimeError("기업 정보가 비어 있습니다.")

def choose_batch(rows, current):
    cutoff=date.today()-timedelta(days=REFRESH_DAYS)
    pending=[]
    for row in rows:
        c=current.get(row["company_id"])
        if not c or c.get("status")=="ERROR":
            pending.append(row); continue
        a=c.get("as_of")
        if not a:
            pending.append(row); continue
        try: d=date.fromisoformat(a)
        except Exception:
            pending.append(row); continue
        if d<=cutoff: pending.append(row)
    pending.sort(key=lambda r:(0 if r["universe"]=="KOSPI_TOP200" else 1,r.get("rank") or 999999))
    return pending[:BATCH_SIZE]

def save_score(row, result, symbol):
    payload={
      "company_id":row["company_id"],"framework_id":"PABRAI_AUTO",
      "total_score":result["total_score"],"coverage_pct":result["coverage_pct"],
      "status":result["status"],
      "details":{**result,"symbol_used":symbol,"universe":row["universe"],"rank":row.get("rank")},
      "as_of":str(date.today())
    }
    supabase.table("framework_scores").upsert(payload,on_conflict="company_id,framework_id").execute()
    supabase.table("framework_score_history").insert(payload).execute()

def save_error(row, symbol, message):
    payload={
      "company_id":row["company_id"],"framework_id":"PABRAI_AUTO",
      "total_score":None,"coverage_pct":0,"status":"ERROR",
      "details":{"symbol_used":symbol,"error":str(message)[:1500],"universe":row["universe"],"rank":row.get("rank")},
      "as_of":str(date.today())
    }
    supabase.table("framework_scores").upsert(payload,on_conflict="company_id,framework_id").execute()

def main():
    rows=get_active_universe()
    print(f"Active universe count: {len(rows)}")
    if len(rows)<700:
        raise RuntimeError(f"활성 Universe가 {len(rows)}개뿐입니다. 먼저 Sync workflow를 실행하세요.")
    current=get_current_scores()
    batch=choose_batch(rows,current)
    print(f"Existing score rows: {len(current)} / Selected batch: {len(batch)}")
    if not batch:
        print("No companies need scoring."); return
    success=failed=0
    for i,row in enumerate(batch,1):
        company=row["companies"]; symbol=yahoo_symbol(company,row["universe"])
        print(f"[{i}/{len(batch)}] {company['ticker']} {company['company_name']} -> {symbol}")
        try:
            info=fetch_info(symbol)
            result=compute_pabrai_auto_score(info)
            save_score(row,result,symbol)
            success+=1
            print(f"  OK score={result['total_score']} coverage={result['coverage_pct']} status={result['status']}")
        except Exception as e:
            failed+=1
            print(f"  ERROR {type(e).__name__}: {e}")
            save_error(row,symbol,e)
        time.sleep(1.0)
    print(f"Batch complete. success={success}, failed={failed}")
    if success==0 and failed>0:
        raise RuntimeError("이번 batch의 모든 기업 데이터 조회가 실패했습니다.")

if __name__=="__main__":
    main()
