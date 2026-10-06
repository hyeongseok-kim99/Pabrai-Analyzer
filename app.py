import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Investment Ranking",
    page_icon="📊",
    layout="wide",
)

@st.cache_resource
def get_supabase():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SECRET_KEY"],
    )

supabase = get_supabase()

@st.cache_data(ttl=300, show_spinner=False)
def get_active_universe():
    result = (
        supabase.table("investment_universe")
        .select("""
            company_id,
            universe,
            rank,
            as_of,
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

@st.cache_data(ttl=300, show_spinner=False)
def get_framework_scores(framework_id):
    result = (
        supabase.table("framework_scores")
        .select("""
            company_id,
            framework_id,
            total_score,
            coverage_pct,
            status,
            as_of,
            details,
            companies (
                id,
                ticker,
                company_name,
                market,
                country
            )
        """)
        .eq("framework_id", framework_id)
        .limit(1000)
        .execute()
    )
    return result.data or []

def build_rank_rows(universe_rows, score_rows):
    score_map = {row["company_id"]: row for row in score_rows}
    rows = []
    for item in universe_rows:
        company = item.get("companies") or {}
        score = score_map.get(item["company_id"])
        rows.append({
            "company_id": item["company_id"],
            "universe": item.get("universe"),
            "universe_rank": item.get("rank"),
            "ticker": company.get("ticker"),
            "company_name": company.get("company_name"),
            "market": company.get("market"),
            "country": company.get("country"),
            "score": score.get("total_score") if score else None,
            "coverage": float(score.get("coverage_pct") or 0) if score else 0.0,
            "status": score.get("status") if score else "PENDING",
            "score_date": score.get("as_of") if score else None,
        })

    scored = [r for r in rows if r["score"] is not None]
    scored.sort(key=lambda r: (-float(r["score"]), -float(r["coverage"]), str(r["ticker"] or "")))
    pending = [r for r in rows if r["score"] is None]
    pending.sort(key=lambda r: (0 if r["universe"] == "KOSPI_TOP200" else 1, r["universe_rank"] or 999999))
    ranked = scored + pending

    rank_no = 0
    for row in ranked:
        if row["score"] is not None:
            rank_no += 1
            row["rank"] = rank_no
        else:
            row["rank"] = None
    return ranked

def filter_rows(rows, search_text, universe_filter, coverage_filter):
    search = search_text.strip().lower()
    filtered = []
    for row in rows:
        if universe_filter != "전체" and row["universe"] != universe_filter:
            continue
        if row["coverage"] < coverage_filter:
            continue
        if search:
            ticker = str(row["ticker"] or "").lower()
            name = str(row["company_name"] or "").lower()
            if search not in ticker and search not in name:
                continue
        filtered.append(row)
    return filtered

def render_ranking_tab(framework_id, title, description, universe_rows, is_ready=True):
    st.subheader(title)
    st.caption(description)

    if not is_ready:
        st.info(
            "평가 Framework의 DB 구조는 준비되어 있습니다. "
            "다음 단계에서 이 관점의 자동 점수 엔진을 연결하면 "
            "Pabrai 탭과 동일한 형태로 703개 기업 랭킹이 표시됩니다."
        )
        return

    try:
        score_rows = get_framework_scores(framework_id)
    except Exception as exc:
        st.error("점수 데이터를 불러오지 못했습니다.")
        st.code(str(exc))
        return

    ranked = build_rank_rows(universe_rows, score_rows)
    total = len(ranked)
    scored_count = sum(1 for row in ranked if row["score"] is not None)
    pending_count = total - scored_count
    scored_values = [float(row["score"]) for row in ranked if row["score"] is not None]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전체 기업", total)
    c2.metric("점수 완료", f"{scored_count}/{total}")
    c3.metric("대기", pending_count)
    c4.metric("평균 Score", f"{sum(scored_values)/len(scored_values):.1f}" if scored_values else "-")

    if total:
        st.progress(scored_count / total, text=f"자동 평가 진행률 {scored_count}/{total} ({scored_count/total*100:.1f}%)")

    st.divider()
    f1, f2, f3 = st.columns([2.2, 1, 1])
    with f1:
        search_text = st.text_input("기업 검색", placeholder="Ticker, 종목코드 또는 기업명", key=f"{framework_id}_search")
    with f2:
        universe_filter = st.selectbox("시장", ["전체", "KOSPI_TOP200", "SP500"], key=f"{framework_id}_universe")
    with f3:
        coverage_filter = st.selectbox("최소 Coverage", [0, 40, 60, 70, 80], index=0, format_func=lambda x: f"{x}% 이상", key=f"{framework_id}_coverage")

    filtered = filter_rows(ranked, search_text, universe_filter, coverage_filter)
    display_rows = []
    for row in filtered:
        score = row["score"]
        display_rows.append({
            "Rank": row["rank"] if score is not None else "-",
            "Ticker": row["ticker"],
            "기업명": row["company_name"],
            "Universe": "KOSPI 200" if row["universe"] == "KOSPI_TOP200" else "S&P 500",
            "Pabrai Score": round(float(score), 1) if score is not None else None,
            "Coverage": f"{row['coverage']:.1f}%" if score is not None else "-",
            "상태": row["status"],
            "기준일": row["score_date"],
        })

    st.dataframe(display_rows, use_container_width=True, hide_index=True, height=700)
    st.caption(
        "Rank는 현재 점수가 계산된 기업끼리의 순위입니다. "
        "아직 자동 평가가 끝나지 않은 기업은 하단에 PENDING으로 표시됩니다. "
        "Pabrai Score는 공개 재무·시장 데이터로 측정 가능한 항목을 정량 프록시로 평가한 1차 스크리닝 점수이며, "
        "Coverage가 낮은 기업은 점수를 보수적으로 해석해야 합니다."
    )

st.title("Investment Ranking")
st.caption("KOSPI 시가총액 상위 200 + S&P 500 구성종목을 투자 철학별로 순위화합니다.")

try:
    universe_rows = get_active_universe()
except Exception as exc:
    st.error("기업 Universe를 불러오지 못했습니다.")
    st.code(str(exc))
    st.stop()

pabrai_tab, buffett_tab, munger_tab = st.tabs(["Pabrai", "Buffett", "Munger"])

with pabrai_tab:
    render_ranking_tab(
        framework_id="PABRAI_AUTO",
        title="Pabrai Ranking",
        description="Leverage → Moat → Management & Ownership을 가장 중요하게 반영한 Pabrai-style 자동 1차 평가 순위",
        universe_rows=universe_rows,
        is_ready=True,
    )

with buffett_tab:
    render_ranking_tab(
        framework_id="BUFFETT_STYLE",
        title="Buffett-style Ranking",
        description="Durable Moat · Business Quality · Capital Allocation · Margin of Safety 중심",
        universe_rows=universe_rows,
        is_ready=False,
    )

with munger_tab:
    render_ranking_tab(
        framework_id="MUNGER_STYLE",
        title="Munger-style Ranking",
        description="Business Quality · Moat · Incentives · Simplicity · Psychology & Risk 중심",
        universe_rows=universe_rows,
        is_ready=False,
    )

st.divider()
st.caption("각 점수는 투자 의사결정을 보조하기 위한 분석 도구이며, 특정 투자자의 공식 점수표를 의미하지 않습니다.")
