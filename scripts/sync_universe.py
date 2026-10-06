from __future__ import annotations

import os
import re
from datetime import date

import FinanceDataReader as fdr
import pandas as pd
from supabase import create_client


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY,
)


def clean_text(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).strip()
    return text or None


def find_col(df, names):
    for name in names:
        if name in df.columns:
            return name
    return None


def is_preferred_korean_name(name: str) -> bool:
    if not name:
        return False
    patterns = [
        r"우$",
        r"우B$",
        r"우C$",
        r"우선주",
        r"\d우$",
    ]
    return any(re.search(p, name) for p in patterns)


def get_kospi_top200():
    # Prefer market-cap listing when available.
    try:
        df = fdr.StockListing("KRX-MARCAP")
    except Exception:
        df = fdr.StockListing("KOSPI")

    symbol_col = find_col(df, ["Code", "Symbol", "Ticker"])
    name_col = find_col(df, ["Name", "Company", "CompanyName"])
    market_col = find_col(df, ["Market", "Exchange"])
    cap_col = find_col(df, ["Marcap", "MarketCap", "Market Cap", "시가총액"])

    if not symbol_col or not name_col:
        raise RuntimeError(f"KOSPI 목록 형식 확인 실패: {list(df.columns)}")

    work = df.copy()

    if market_col:
        values = work[market_col].astype(str).str.upper()
        if values.str.contains("KOSPI").any():
            work = work[values.str.contains("KOSPI")]

    work["_symbol"] = work[symbol_col].astype(str).str.zfill(6)
    work["_name"] = work[name_col].astype(str).str.strip()

    work = work[
        ~work["_name"].map(is_preferred_korean_name)
    ]

    if cap_col:
        work["_cap"] = pd.to_numeric(
            work[cap_col],
            errors="coerce",
        )
        work = work.sort_values(
            "_cap",
            ascending=False,
            na_position="last",
        )

    work = work.drop_duplicates(
        subset=["_name"],
        keep="first",
    ).head(200)

    rows = []
    for rank, (_, row) in enumerate(work.iterrows(), start=1):
        rows.append(
            {
                "ticker": row["_symbol"],
                "company_name": row["_name"],
                "market": "KOSPI",
                "country": "South Korea",
                "rank": rank,
                "universe": "KOSPI_TOP200",
            }
        )

    if len(rows) != 200:
        raise RuntimeError(
            f"KOSPI 상위 200개를 확보하지 못했습니다. 현재 {len(rows)}개"
        )

    return rows


def get_sp500():
    df = fdr.StockListing("S&P500")

    symbol_col = find_col(df, ["Symbol", "Code", "Ticker"])
    name_col = find_col(df, ["Name", "Company", "CompanyName"])
    exchange_col = find_col(df, ["Exchange", "Market"])

    if not symbol_col or not name_col:
        raise RuntimeError(f"S&P500 목록 형식 확인 실패: {list(df.columns)}")

    rows = []
    for rank, (_, row) in enumerate(df.iterrows(), start=1):
        ticker = clean_text(row.get(symbol_col))
        name = clean_text(row.get(name_col))
        if not ticker or not name:
            continue

        exchange = clean_text(row.get(exchange_col)) if exchange_col else None

        rows.append(
            {
                "ticker": ticker.upper(),
                "company_name": name,
                "market": exchange or "US",
                "country": "USA",
                "rank": rank,
                "universe": "SP500",
            }
        )

    # S&P DJI currently reports 503 constituents because some companies have
    # multiple listed share classes. Do not silently truncate the source list.
    if len(rows) < 500:
        raise RuntimeError(
            f"S&P500 구성종목 수가 비정상적으로 적습니다: {len(rows)}"
        )

    return rows


def find_company(ticker):
    result = (
        supabase
        .table("companies")
        .select("*")
        .eq("ticker", ticker)
        .limit(10)
        .execute()
    )
    return (result.data or [None])[0]


def ensure_company(item):
    existing = find_company(item["ticker"])

    payload = {
        "ticker": item["ticker"],
        "company_name": item["company_name"],
        "market": item["market"],
        "country": item["country"],
    }

    if existing:
        (
            supabase
            .table("companies")
            .update(payload)
            .eq("id", existing["id"])
            .execute()
        )
        return existing["id"]

    result = (
        supabase
        .table("companies")
        .insert(payload)
        .execute()
    )
    if not result.data:
        raise RuntimeError(f"기업 추가 실패: {item['ticker']}")
    return result.data[0]["id"]


def replace_universe(universe, rows):
    today = str(date.today())

    (
        supabase
        .table("investment_universe")
        .update({"is_active": False})
        .eq("universe", universe)
        .execute()
    )

    upserts = []
    for item in rows:
        company_id = ensure_company(item)
        upserts.append(
            {
                "company_id": company_id,
                "universe": universe,
                "rank": item["rank"],
                "is_active": True,
                "as_of": today,
            }
        )

    for start in range(0, len(upserts), 200):
        (
            supabase
            .table("investment_universe")
            .upsert(
                upserts[start:start + 200],
                on_conflict="company_id,universe",
            )
            .execute()
        )

    return len(upserts)


def main():
    kospi = get_kospi_top200()
    sp500 = get_sp500()

    kospi_count = replace_universe(
        "KOSPI_TOP200",
        kospi,
    )
    sp_count = replace_universe(
        "SP500",
        sp500,
    )

    print(
        f"Universe sync complete: "
        f"KOSPI_TOP200={kospi_count}, "
        f"SP500={sp_count}, "
        f"TOTAL={kospi_count + sp_count}"
    )


if __name__ == "__main__":
    main()
