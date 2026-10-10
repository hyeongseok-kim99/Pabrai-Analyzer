from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
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
RETRY_ONLY = os.environ.get("RETRY_ONLY", "0") == "1"
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
        .select("company_id,as_of,status,total_score")
        .eq("framework_id", "PABRAI_AUTO")
        .limit(1000)
        .execute()
    )
    return {row["company_id"]: row for row in (result.data or [])}


def symbol_candidates(company, universe):
    ticker = str(company["ticker"]).strip().upper()

    if universe == "KOSPI_TOP200":
        return [f"{ticker}.KS"]

    candidates = [
        ticker.replace(".", "-"),
        ticker,
        ticker.replace("/", "-"),
    ]

    result = []
    for symbol in candidates:
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def _safe_float(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _first_value(df, labels, column_index=0):
    if df is None or getattr(df, "empty", True):
        return None

    for label in labels:
        if label not in df.index:
            continue
        try:
            series = df.loc[label]
            if hasattr(series, "iloc"):
                return _safe_float(series.iloc[column_index])
            return _safe_float(series)
        except Exception:
            continue
    return None


def _growth_from_statement(df, labels):
    latest = _first_value(df, labels, 0)
    previous = _first_value(df, labels, 1)
    if latest is None or previous in (None, 0):
        return None
    return latest / previous - 1.0


def _fallback_info(ticker_obj):
    """Build a partial Yahoo-compatible info dict from statements/fast_info."""
    result = {}

    try:
        fast = ticker_obj.fast_info
        for source_key, target_key in [
            ("market_cap", "marketCap"),
        ]:
            try:
                value = _safe_float(fast[source_key])
                if value is not None:
                    result[target_key] = value
            except Exception:
                pass
    except Exception:
        pass

    try:
        income = ticker_obj.income_stmt
    except Exception:
        income = None

    try:
        balance = ticker_obj.balance_sheet
    except Exception:
        balance = None

    try:
        cashflow = ticker_obj.cashflow
    except Exception:
        cashflow = None

    revenue = _first_value(income, ["Total Revenue", "Operating Revenue"])
    gross_profit = _first_value(income, ["Gross Profit"])
    operating_income = _first_value(income, ["Operating Income"])
    net_income = _first_value(
        income,
        ["Net Income Common Stockholders", "Net Income", "Net Income Including Noncontrolling Interests"],
    )
    ebitda = _first_value(income, ["EBITDA", "Normalized EBITDA"])

    total_assets = _first_value(balance, ["Total Assets"])
    equity = _first_value(
        balance,
        ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
    )
    total_debt = _first_value(balance, ["Total Debt"])
    cash = _first_value(
        balance,
        [
            "Cash Cash Equivalents And Short Term Investments",
            "Cash And Cash Equivalents",
            "Cash Financial",
        ],
    )
    current_assets = _first_value(balance, ["Current Assets", "Total Current Assets"])
    current_liabilities = _first_value(balance, ["Current Liabilities", "Total Current Liabilities"])

    operating_cf = _first_value(
        cashflow,
        ["Operating Cash Flow", "Total Cash From Operating Activities"],
    )
    capex = _first_value(
        cashflow,
        ["Capital Expenditure", "Capital Expenditures"],
    )

    free_cf = None
    if operating_cf is not None:
        if capex is None:
            free_cf = operating_cf
        else:
            # yfinance normally reports capex as a negative cash-flow value.
            free_cf = operating_cf + capex if capex < 0 else operating_cf - capex

    def set_if(key, value):
        value = _safe_float(value)
        if value is not None:
            result[key] = value

    set_if("totalRevenue", revenue)
    set_if("netIncomeToCommon", net_income)
    set_if("ebitda", ebitda)
    set_if("totalDebt", total_debt)
    set_if("totalCash", cash)
    set_if("operatingCashflow", operating_cf)
    set_if("freeCashflow", free_cf)

    if revenue not in (None, 0):
        if gross_profit is not None:
            set_if("grossMargins", gross_profit / revenue)
        if operating_income is not None:
            set_if("operatingMargins", operating_income / revenue)
        if net_income is not None:
            set_if("profitMargins", net_income / revenue)

    if net_income is not None and total_assets not in (None, 0):
        set_if("returnOnAssets", net_income / total_assets)

    if net_income is not None and equity not in (None, 0):
        set_if("returnOnEquity", net_income / equity)

    if total_debt is not None and equity not in (None, 0):
        # Yahoo debtToEquity is commonly reported in percentage points.
        set_if("debtToEquity", total_debt / equity * 100.0)

    if current_assets is not None and current_liabilities not in (None, 0):
        set_if("currentRatio", current_assets / current_liabilities)

    set_if("revenueGrowth", _growth_from_statement(income, ["Total Revenue", "Operating Revenue"]))
    set_if(
        "earningsGrowth",
        _growth_from_statement(
            income,
            ["Net Income Common Stockholders", "Net Income", "Net Income Including Noncontrolling Interests"],
        ),
    )

    market_cap = _safe_float(result.get("marketCap"))
    if market_cap is not None and net_income is not None and net_income > 0:
        set_if("trailingPE", market_cap / net_income)

    if market_cap is not None and equity is not None and equity != 0:
        set_if("priceToBook", market_cap / equity)

    if market_cap is not None and ebitda is not None and ebitda > 0:
        enterprise_value = market_cap + (total_debt or 0.0) - (cash or 0.0)
        set_if("enterpriseToEbitda", enterprise_value / ebitda)

    return result


CRITICAL_KEYS = [
    "marketCap",
    "totalRevenue",
    "freeCashflow",
    "totalDebt",
    "totalCash",
    "ebitda",
    "grossMargins",
    "operatingMargins",
    "returnOnEquity",
    "trailingPE",
    "priceToBook",
]


def _info_quality(info):
    return sum(1 for key in CRITICAL_KEYS if _safe_float(info.get(key)) is not None)


def fetch_info_for_company(company, universe, attempts=4):
    best_info = None
    best_symbol = None
    best_quality = -1
    errors = []

    for symbol in symbol_candidates(company, universe):
        ticker_obj = yf.Ticker(symbol)
        base_info = {}

        for attempt in range(attempts):
            try:
                candidate = ticker_obj.get_info()
                if candidate and isinstance(candidate, dict):
                    base_info.update(candidate)
                    break
            except Exception as exc:
                errors.append(f"{symbol}/get_info#{attempt + 1}: {exc}")
                time.sleep(2 ** attempt)

        # If normal info is sparse or unavailable, synthesize quantitative
        # metrics from Yahoo financial statements and fast_info.
        if _info_quality(base_info) < 7:
            try:
                fallback = _fallback_info(ticker_obj)
                for key, value in fallback.items():
                    if base_info.get(key) is None:
                        base_info[key] = value
            except Exception as exc:
                errors.append(f"{symbol}/fallback: {exc}")

        quality = _info_quality(base_info)
        if quality > best_quality:
            best_info = base_info
            best_symbol = symbol
            best_quality = quality

        # This is enough to generate a meaningful preliminary score.
        if quality >= 7:
            break

    if best_info and best_quality > 0:
        return best_info, best_symbol, best_quality

    message = " | ".join(errors[-6:]) if errors else "기업 정보가 비어 있습니다."
    raise RuntimeError(message)


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
        current = current_scores.get(row["company_id"])

        if RETRY_ONLY:
            if not current or current.get("status") == "ERROR" or current.get("total_score") is None:
                candidates.append(row)
            continue

        if FORCE_REFRESH:
            candidates.append(row)
            continue

        if not current or current.get("status") == "ERROR" or current.get("total_score") is None:
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


def save_score(row, result, symbol, info_quality):
    payload = {
        "company_id": row["company_id"],
        "framework_id": "PABRAI_AUTO",
        "total_score": result["total_score"],
        "coverage_pct": result["coverage_pct"],
        "status": result["status"],
        "details": {
            **result,
            "symbol_used": symbol,
            "info_quality_keys": info_quality,
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


def save_error(row, symbols, error):
    payload = {
        "company_id": row["company_id"],
        "framework_id": "PABRAI_AUTO",
        "total_score": None,
        "coverage_pct": 0,
        "status": "ERROR",
        "details": {
            "symbols_tried": symbols,
            "error": str(error)[:2500],
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
    print(
        f"Shard: {SHARD_INDEX}/{SHARD_COUNT} | force={FORCE_REFRESH} | "
        f"retry_only={RETRY_ONLY}"
    )

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
        candidates = symbol_candidates(company, row["universe"])
        print(
            f"[{index}/{len(batch)}] {company['ticker']} {company['company_name']} "
            f"candidates={candidates}"
        )

        try:
            info, symbol, quality = fetch_info_for_company(company, row["universe"])
            result = compute_pabrai_auto_score(info)
            qdetail = build_question_breakdown(questions, result)
            result["question_breakdown"] = qdetail["rows"]
            result["question_coverage_pct"] = qdetail["question_coverage_pct"]
            result["auto_scored_question_count"] = qdetail["auto_scored_count"]
            result["qualitative_question_count"] = qdetail["qualitative_count"]
            result["data_missing_question_count"] = qdetail["data_missing_count"]
            save_score(row, result, symbol, quality)
            success += 1
            print(
                f"  OK symbol={symbol} quality={quality} score={result['total_score']} "
                f"coverage={result['coverage_pct']} status={result['status']}"
            )
        except Exception as exc:
            failed += 1
            print(f"  ERROR {type(exc).__name__}: {exc}")
            save_error(row, candidates, exc)

        time.sleep(REQUEST_DELAY)

    print(f"Batch complete. success={success}, failed={failed}")
    # Do not fail the entire workflow for a few truly unavailable tickers.
    # Their ERROR details remain visible and the next retry workflow can try again.


if __name__ == "__main__":
    main()
