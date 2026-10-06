from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta

import yfinance as yf
from supabase import create_client

from scoring_engine import compute_pabrai_auto_score


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
BATCH_SIZE = int(os.environ.get("BATCH_SIZE") or "30")
REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS") or "30")

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY,
)


def get_active_universe():
    result = (
        supabase
        .table("investment_universe")
        .select(
            """
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
            """
        )
        .eq("is_active", True)
        .limit(1000)
        .execute()
    )
    return result.data or []


def get_current_scores():
    result = (
        supabase
        .table("framework_scores")
        .select("company_id,as_of,status")
        .eq("framework_id", "PABRAI_AUTO")
        .limit(1000)
        .execute()
    )
    return {
        row["company_id"]: row
        for row in (result.data or [])
    }


def yahoo_symbol(company, universe):
    ticker = company["ticker"]

    if universe == "KOSPI_TOP200":
        return f"{ticker}.KS"

    # Yahoo commonly uses '-' where index providers use '.' for share classes.
    return ticker.replace(".", "-")


def fetch_info(symbol, attempts=3):
    last_error = None

    for attempt in range(attempts):
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.get_info()
            if info:
                return info
        except Exception as e:
            last_error = e

        time.sleep(2 ** attempt)

    if last_error:
        raise last_error

    raise RuntimeError("빈 기업 정보")


def choose_batch(universe_rows, current_scores):
    cutoff = date.today() - timedelta(days=REFRESH_DAYS)

    pending = []

    for row in universe_rows:
        company_id = row["company_id"]
        current = current_scores.get(company_id)

        if not current:
            pending.append(row)
            continue

        as_of = current.get("as_of")
        if not as_of:
            pending.append(row)
            continue

        try:
            score_date = date.fromisoformat(as_of)
        except Exception:
            pending.append(row)
            continue

        if score_date <= cutoff:
            pending.append(row)

    pending.sort(
        key=lambda x: (
            0 if x["universe"] == "KOSPI_TOP200" else 1,
            x.get("rank") or 999999,
        )
    )

    return pending[:BATCH_SIZE]


def save_score(row, result, symbol):
    company_id = row["company_id"]
    today = str(date.today())

    details = {
        **result,
        "symbol_used": symbol,
        "universe": row["universe"],
        "rank": row.get("rank"),
    }

    current_payload = {
        "company_id": company_id,
        "framework_id": "PABRAI_AUTO",
        "total_score": result["total_score"],
        "coverage_pct": result["coverage_pct"],
        "status": result["status"],
        "details": details,
        "as_of": today,
    }

    (
        supabase
        .table("framework_scores")
        .upsert(
            current_payload,
            on_conflict="company_id,framework_id",
        )
        .execute()
    )

    (
        supabase
        .table("framework_score_history")
        .insert(current_payload)
        .execute()
    )


def save_error(row, symbol, message):
    company_id = row["company_id"]
    payload = {
        "company_id": company_id,
        "framework_id": "PABRAI_AUTO",
        "total_score": None,
        "coverage_pct": 0,
        "status": "ERROR",
        "details": {
            "symbol_used": symbol,
            "error": message[:1500],
            "universe": row["universe"],
        },
        "as_of": str(date.today()),
    }
    (
        supabase
        .table("framework_scores")
        .upsert(
            payload,
            on_conflict="company_id,framework_id",
        )
        .execute()
    )


def main():
    universe_rows = get_active_universe()

    if len(universe_rows) < 700:
        raise RuntimeError(
            f"활성 Universe가 {len(universe_rows)}개뿐입니다. "
            "먼저 sync_universe.py를 실행하세요."
        )

    current_scores = get_current_scores()
    batch = choose_batch(
        universe_rows,
        current_scores,
    )

    if not batch:
        print("No companies need scoring.")
        return

    success = 0
    failed = 0

    for index, row in enumerate(batch, start=1):
        company = row["companies"]
        symbol = yahoo_symbol(
            company,
            row["universe"],
        )

        print(
            f"[{index}/{len(batch)}] "
            f"{company['ticker']} {company['company_name']} "
            f"-> {symbol}"
        )

        try:
            info = fetch_info(symbol)
            result = compute_pabrai_auto_score(info)
            save_score(
                row,
                result,
                symbol,
            )
            success += 1

            print(
                f"  score={result['total_score']} "
                f"coverage={result['coverage_pct']} "
                f"status={result['status']}"
            )

        except Exception as e:
            failed += 1
            print(f"  ERROR: {e}")
            save_error(
                row,
                symbol,
                str(e),
            )

        time.sleep(1.0)

    print(
        f"Batch complete. success={success}, failed={failed}"
    )


if __name__ == "__main__":
    main()
