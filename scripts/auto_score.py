from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf
from supabase import create_client

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scoring_engine import compute_pabrai_auto_score, build_question_breakdown

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
BATCH_SIZE = int(os.environ.get("BATCH_SIZE") or "30")
REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS") or "30")
FORCE_REFRESH = os.environ.get("FORCE_REFRESH", "0") == "1"
SHARD_COUNT = max(1, int(os.environ.get("SHARD_COUNT") or "1"))
SHARD_INDEX = int(os.environ.get("SHARD_INDEX") or "0")
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY") or "1.2")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)


def get_active_universe():
    result = (
        supabase.table("investment_universe")
        .select("""
            company_id,
            universe,
            rank,
            companies (
                id,
                ticker,
                company_name,
                market,
                country
            )
        """)
        .eq("is_active", True)
        .limit(1000)
        .execute()
    )
    return result.data or []


def get_questions():
    result = (
        supabase.table("questions")
        .select("question_no,category,source_grade,weight_pct")
        .eq("is_active", True)
        .order("question_no")
        .execute()
    )
    return result.data or []


def get_current_scores():
    result = (
        supabase.table("framework_scores")
        .select("company_id,as_of,status")
        .eq("framework_id", "PABRAI_AUTO")
        .limit(1000)
        .execute()
    )
    return {row["company_id"]: row for row in (result.data or [])}


def yahoo_symbol(company, universe):
    ticker = str(company["ticker"]).strip()
    if universe == "KOSPI_TOP200":
        return f"{ticker}.KS"
    return ticker.replace(".", "-")


def fetch_info(symbol, attempts=4):
    last_error = None
    for attempt in range(attempts):
        try:
            info = yf.Ticker(symbol).get_info()
            if info and isinstance(info, dict):
                return info
        except Exception as exc:
            last_error = exc
        time.sleep(2 ** attempt)
    if last_error:
        raise last_error
    raise RuntimeError("기업 정보가 비어 있습니다.")


def choose_batch(universe_rows, current_scores):
    cutoff = date.today() - timedelta(days=REFRESH_DAYS)
    candidates = []

    universe_rows = sorted(
        universe_rows,
        key=lambda row: (
            0 if row["universe"] == "KOSPI_TOP200" else 1,
            row.get("rank") or 999999,
            str((row.get("companies") or {}).get("ticker") or ""),
        ),
    )

    for row in universe_rows:
        if FORCE_REFRESH:
            candidates.append(row)
            continue

        current = current_scores.get(row["company_id"])
        if not current or current.get("status") == "ERROR":
            candidates.append(row)
            continue

        as_of = current.get("as_of")
        if not as_of:
            candidates.append(row)
            continue

        try:
            score_date = date.fromisoformat(as_of)
        except Exception:
            candidates.append(row)
            continue

        if score_date <= cutoff:
            candidates.append(row)

    sharded = [
        row for idx, row in enumerate(candidates)
        if idx % SHARD_COUNT == SHARD_INDEX
    ]
    return sharded[:BATCH_SIZE]


def save_score(row, result, symbol):
    payload = {
        "company_id": row["company_id"],
        "framework_id": "PABRAI_AUTO",
        "total_score": result["total_score"],
        "coverage_pct": result["coverage_pct"],
        "status": result["status"],
        "details": {
            **result,
            "symbol_used": symbol,
            "universe": row["universe"],
            "rank": row.get("rank"),
        },
        "as_of": str(date.today()),
    }
    supabase.table("framework_scores").upsert(
        payload,
        on_conflict="company_id,framework_id",
    ).execute()
    supabase.table("framework_score_history").insert(payload).execute()


def save_error(row, symbol, error):
    payload = {
        "company_id": row["company_id"],
        "framework_id": "PABRAI_AUTO",
        "total_score": None,
        "coverage_pct": 0,
        "status": "ERROR",
        "details": {
            "symbol_used": symbol,
            "error": str(error)[:1500],
            "universe": row["universe"],
            "rank": row.get("rank"),
        },
        "as_of": str(date.today()),
    }
    supabase.table("framework_scores").upsert(
        payload,
        on_conflict="company_id,framework_id",
    ).execute()


def main():
    universe_rows = get_active_universe()
    questions = get_questions()
    print(f"Active universe count: {len(universe_rows)}")
    print(f"Question count: {len(questions)}")
    print(f"Shard: {SHARD_INDEX}/{SHARD_COUNT} | force={FORCE_REFRESH}")

    if len(universe_rows) < 700:
        raise RuntimeError(f"활성 Universe가 {len(universe_rows)}개뿐입니다.")
    if len(questions) != 213:
        raise RuntimeError(f"질문이 {len(questions)}개입니다. 213개가 필요합니다.")

    current_scores = get_current_scores()
    batch = choose_batch(universe_rows, current_scores)
    print(f"Existing scores: {len(current_scores)} | Selected batch: {len(batch)}")

    if not batch:
        print("No companies need scoring.")
        return

    success = 0
    failed = 0

    for index, row in enumerate(batch, start=1):
        company = row["companies"]
        symbol = yahoo_symbol(company, row["universe"])
        print(f"[{index}/{len(batch)}] {company['ticker']} {company['company_name']} -> {symbol}")

        try:
            info = fetch_info(symbol)
            result = compute_pabrai_auto_score(info)
            qdetail = build_question_breakdown(questions, result)
            result["question_breakdown"] = qdetail["rows"]
            result["question_coverage_pct"] = qdetail["question_coverage_pct"]
            result["auto_scored_question_count"] = qdetail["auto_scored_count"]
            result["qualitative_question_count"] = qdetail["qualitative_count"]
            result["data_missing_question_count"] = qdetail["data_missing_count"]
            save_score(row, result, symbol)
            success += 1
            print(
                f"  OK score={result['total_score']} coverage={result['coverage_pct']} "
                f"questions={result['auto_scored_question_count']}/213 status={result['status']}"
            )
        except Exception as exc:
            failed += 1
            print(f"  ERROR {type(exc).__name__}: {exc}")
            save_error(row, symbol, exc)

        time.sleep(REQUEST_DELAY)

    print(f"Batch complete. success={success}, failed={failed}")
    if success == 0 and failed > 0:
        raise RuntimeError("이번 batch의 모든 기업 데이터 조회가 실패했습니다.")


if __name__ == "__main__":
    main()
