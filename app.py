import streamlit as st
from supabase import create_client

from scoring_engine import build_question_breakdown

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


@st.cache_data(ttl=300, show_spinner=False)
def get_questions():
    result = (
        supabase.table("questions")
        .select("question_no,category,source_grade,question,weight_pct")
        .eq("is_active", True)
        .order("question_no")
        .execute()
    )
    return result.data or []


def _safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_multiple(value):
    value = _safe_float(value)
    if value is None:
        return None
    return round(value, 2)


def build_rank_rows(universe_rows, score_rows):
    score_map = {row["company_id"]: row for row in score_rows}
    rows = []

    for item in universe_rows:
        company = item.get("companies") or {}
        score = score_map.get(item["company_id"])
        details = (score or {}).get("details") or {}
        raw_metrics = details.get("raw_metrics") or {}
        coverage = float((score or {}).get("coverage_pct") or 0)
        status = (score or {}).get("status") or "PENDING"
        total_score = (score or {}).get("total_score")

        # Rank is only considered reliable when the model has >=70% weighted
        # quantitative coverage (AUTO_PRELIM). Lower-coverage scores remain
        # visible but are intentionally left unranked.
        rank_eligible = (
            total_score is not None
            and status == "AUTO_PRELIM"
            and coverage >= 70
        )

        rows.append({
            "company_id": item["company_id"],
            "universe": item.get("universe"),
            "universe_rank": item.get("rank"),
            "ticker": company.get("ticker"),
            "company_name": company.get("company_name"),
            "market": company.get("market"),
            "country": company.get("country"),
            "score": total_score,
            "coverage": coverage,
            "status": status,
            "score_date": (score or {}).get("as_of"),
            "per": _safe_float(raw_metrics.get("trailingPE")),
            "pbr": _safe_float(raw_metrics.get("priceToBook")),
            "rank_eligible": rank_eligible,
        })

    ranked_valid = [row for row in rows if row["rank_eligible"]]
    ranked_valid.sort(
        key=lambda row: (
            -float(row["score"]),
            -float(row["coverage"]),
            str(row["ticker"] or ""),
        )
    )

    scored_low_coverage = [
        row for row in rows
        if row["score"] is not None and not row["rank_eligible"]
    ]
    scored_low_coverage.sort(
        key=lambda row: (
            -float(row["coverage"]),
            -float(row["score"]),
            str(row["ticker"] or ""),
        )
    )

    pending = [row for row in rows if row["score"] is None]
    pending.sort(
        key=lambda row: (
            0 if row["universe"] == "KOSPI_TOP200" else 1,
            row["universe_rank"] or 999999,
        )
    )

    for rank_no, row in enumerate(ranked_valid, start=1):
        row["rank"] = rank_no

    for row in scored_low_coverage + pending:
        row["rank"] = None

    return ranked_valid + scored_low_coverage + pending

def render_pabrai_ranking(universe_rows, score_rows):
    st.subheader("Pabrai Ranking")
    st.caption(
        "Leverage → Moat → Management & Ownership을 가장 중요하게 반영한 "
        "Pabrai-style 자동 1차 평가 순위"
    )

    ranked = build_rank_rows(universe_rows, score_rows)
    total = len(ranked)
    scored_count = sum(1 for row in ranked if row["score"] is not None)
    ranked_count = sum(1 for row in ranked if row["rank_eligible"])
    pending_count = total - scored_count
    score_values = [
        float(row["score"])
        for row in ranked
        if row["rank_eligible"]
    ]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("전체 기업", total)
    c2.metric("점수 완료", f"{scored_count}/{total}")
    c3.metric("신뢰 랭킹", ranked_count)
    c4.metric("대기", pending_count)
    c5.metric(
        "평균 Score",
        f"{sum(score_values) / len(score_values):.1f}" if score_values else "-",
    )

    if total:
        st.progress(
            scored_count / total,
            text=f"자동 평가 진행률 {scored_count}/{total} ({scored_count / total * 100:.1f}%)",
        )

    with st.expander("상태와 Coverage 기준 보기"):
        st.markdown(
            """
- **AUTO_PRELIM**: 정량 Factor 가중치의 **70% 이상**을 실제 데이터로 평가했습니다. 이 상태만 공식 Rank에 포함합니다.
- **LOW_COVERAGE**: Coverage가 **45% 이상 70% 미만**입니다. 참고 점수는 표시하지만 Rank에는 넣지 않습니다.
- **INSUFFICIENT_DATA**: Coverage가 **45% 미만**입니다. 일부 데이터만으로 계산된 점수라 신뢰도가 낮아 Rank에서 제외합니다.
- **PENDING / ERROR**: 아직 계산되지 않았거나 외부 데이터 조회에 실패한 기업입니다.

**Coverage**는 213문항 중 답한 비율이 아니라, 자동 Pabrai Score에 연결된 정량 Factor의 전체 가중치 100% 중 실제 데이터가 확보되어 계산에 사용된 가중치 비율입니다.
            """
        )

    st.divider()

    f1, f2, f3 = st.columns([2.2, 1, 1])
    with f1:
        search = st.text_input(
            "기업 검색",
            placeholder="Ticker, 종목코드 또는 기업명",
            key="pabrai_rank_search",
        ).strip().lower()
    with f2:
        universe_filter = st.selectbox(
            "시장",
            ["전체", "KOSPI_TOP200", "SP500"],
            key="pabrai_rank_market",
        )
    with f3:
        min_coverage = st.selectbox(
            "최소 Coverage",
            [0, 40, 60, 70, 80],
            format_func=lambda value: f"{value}% 이상",
            key="pabrai_rank_coverage",
        )

    filtered = []
    for row in ranked:
        if universe_filter != "전체" and row["universe"] != universe_filter:
            continue
        if row["coverage"] < min_coverage:
            continue
        if search:
            text = f"{row['ticker']} {row['company_name']}".lower()
            if search not in text:
                continue
        filtered.append(row)

    st.dataframe(
        [
            {
                "Rank": row["rank"] if row["rank_eligible"] else "-",
                "Ticker": row["ticker"],
                "기업명": row["company_name"],
                "Universe": "KOSPI 200" if row["universe"] == "KOSPI_TOP200" else "S&P 500",
                "Pabrai Score": round(float(row["score"]), 1) if row["score"] is not None else None,
                "PER": _fmt_multiple(row["per"]),
                "PBR": _fmt_multiple(row["pbr"]),
                "Coverage": f"{row['coverage']:.1f}%" if row["score"] is not None else "-",
                "상태": row["status"],
                "기준일": row["score_date"],
            }
            for row in filtered
        ],
        use_container_width=True,
        hide_index=True,
        height=700,
    )

    st.caption(
        "Rank는 AUTO_PRELIM(정량 Coverage 70% 이상) 기업만 대상으로 계산합니다. "
        "LOW_COVERAGE / INSUFFICIENT_DATA 기업도 점수·PER·PBR은 확인할 수 있지만 순위에서는 제외됩니다."
    )

def render_pabrai2(score_rows, questions):
    st.subheader("Pabrai2 · 213문항 상세")
    st.caption(
        "기업별 213개 질문을 펼쳐보고, 자동으로 정량화 가능한 문항은 0~5점 프록시 점수와 근거 Factor를 확인합니다."
    )

    ticker_input = st.text_input(
        "종목코드 / Ticker",
        placeholder="005930 또는 AAPL",
        key="pabrai2_ticker",
    ).strip().upper()

    if not ticker_input:
        st.info("종목코드 또는 Ticker를 입력하면 213문항 상세를 표시합니다.")
        return

    selected = None
    for row in score_rows:
        company = row.get("companies") or {}
        if str(company.get("ticker") or "").upper() == ticker_input:
            selected = row
            break

    if not selected:
        st.warning("아직 해당 기업의 Auto Pabrai Score가 계산되지 않았습니다.")
        return

    company = selected.get("companies") or {}
    details = selected.get("details") or {}
    breakdown = details.get("question_breakdown") or []

    # Older score rows may predate the Pabrai2 stored breakdown.
    # If factor data is already present, build the 213-question detail in memory
    # so a full re-score is not required just to view Pabrai2.
    if not breakdown and details.get("factors"):
        qdetail = build_question_breakdown(questions, details)
        breakdown = qdetail["rows"]
        details = {
            **details,
            "question_coverage_pct": qdetail["question_coverage_pct"],
            "auto_scored_question_count": qdetail["auto_scored_count"],
            "qualitative_question_count": qdetail["qualitative_count"],
            "data_missing_question_count": qdetail["data_missing_count"],
        }

    breakdown_map = {int(row["question_no"]): row for row in breakdown}
    raw_metrics = details.get("raw_metrics") or {}
    per_value = _safe_float(raw_metrics.get("trailingPE"))
    pbr_value = _safe_float(raw_metrics.get("priceToBook"))

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("기업", f"{company.get('company_name')} ({company.get('ticker')})")
    c2.metric("Pabrai Score", selected.get("total_score") if selected.get("total_score") is not None else "-")
    c3.metric("PER", f"{per_value:.2f}" if per_value is not None else "-")
    c4.metric("PBR", f"{pbr_value:.2f}" if pbr_value is not None else "-")
    c5.metric("Factor Coverage", f"{float(selected.get('coverage_pct') or 0):.1f}%")
    c6.metric("문항 Auto Coverage", f"{float(details.get('question_coverage_pct') or 0):.1f}%")

    auto_count = int(details.get("auto_scored_question_count") or 0)
    qualitative_count = int(details.get("qualitative_question_count") or 0)
    missing_count = int(details.get("data_missing_question_count") or 0)

    st.write(
        f"자동 프록시 평가 **{auto_count}문항** · "
        f"정성평가 필요 **{qualitative_count}문항** · "
        f"데이터 부족 **{missing_count}문항**"
    )

    if not breakdown:
        st.warning(
            "이 기업 점수는 Pabrai2 상세 기능 추가 전 계산된 데이터입니다. "
            "'Score all 703 companies now'를 실행해 재계산하면 상세가 생성됩니다."
        )
        return

    categories = ["전체"] + list(dict.fromkeys(q["category"] for q in questions))
    f1, f2 = st.columns(2)
    with f1:
        category_filter = st.selectbox("Category", categories, key="pabrai2_category")
    with f2:
        status_filter = st.selectbox(
            "평가 상태",
            ["전체", "AUTO_PROXY", "NEEDS_QUALITATIVE", "DATA_MISSING"],
            key="pabrai2_status",
        )

    rows = []
    for question in questions:
        qno = int(question["question_no"])
        detail = breakdown_map.get(qno, {})
        status = detail.get("status", "NEEDS_QUALITATIVE")

        if category_filter != "전체" and question["category"] != category_filter:
            continue
        if status_filter != "전체" and status != status_filter:
            continue

        rows.append(
            {
                "No": qno,
                "Category": question["category"],
                "등급": question["source_grade"],
                "질문": question["question"],
                "가중치": f"{float(question['weight_pct']):.3f}%",
                "자동점수(0~5)": detail.get("score_5"),
                "상태": status,
                "자동 근거": detail.get("basis", "정성평가 필요"),
            }
        )

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        height=720,
    )

    st.warning(
        "AUTO_PROXY는 공개 재무·시장 수치를 질문의 대리변수로 사용한 점수입니다. "
        "Moat의 실제 지속성, 경영진의 정직성, Circle of Competence, Personal Biases 같은 문항은 "
        "숫자만으로 임의 채점하지 않고 정성평가 대상으로 남깁니다."
    )


st.title("Investment Ranking")
st.caption("KOSPI 시가총액 상위 200 + S&P 500 구성종목을 투자 철학별로 순위화합니다.")

try:
    universe_rows = get_active_universe()
    pabrai_scores = get_framework_scores("PABRAI_AUTO")
    questions = get_questions()
except Exception as exc:
    st.error("Supabase 데이터를 불러오지 못했습니다.")
    st.code(str(exc))
    st.stop()

pabrai_tab, pabrai2_tab, buffett_tab, munger_tab = st.tabs(
    ["Pabrai", "Pabrai2", "Buffett", "Munger"]
)

with pabrai_tab:
    render_pabrai_ranking(universe_rows, pabrai_scores)

with pabrai2_tab:
    render_pabrai2(pabrai_scores, questions)

with buffett_tab:
    st.subheader("Buffett-style Ranking")
    st.info("다음 단계에서 Buffett-style 점수 엔진을 연결하면 동일한 703개 기업 랭킹을 표시합니다.")

with munger_tab:
    st.subheader("Munger-style Ranking")
    st.info("다음 단계에서 Munger-style 점수 엔진을 연결하면 동일한 703개 기업 랭킹을 표시합니다.")

st.divider()
st.caption(
    "Pabrai Score와 문항별 Auto Proxy는 특정 투자자의 공식 점수표가 아니라 투자 의사결정 보조용 모델입니다."
)
